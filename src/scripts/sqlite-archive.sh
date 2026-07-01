#!/bin/sh
# sqlite-archive.sh — SQLite-safe volume archive for the "sqlite" backup_strategy.
#
# Produces a transactionally-consistent .tar.gz of a docker volume that holds one
# or more live SQLite databases. Instead of a raw `tar` of the live files — which
# can capture a torn / inconsistent page set while the owning service is mid-write
# (lucos_backups#344) — it snapshots each database with SQLite's online backup API
# (`.backup`), which copies a consistent image even under concurrent writes.
#
# Runs inside the versioned lucos_backups image (which ships the sqlite CLI), the
# same source-side helper-container delivery pattern as the incremental strategy's
# rsync. The source volume is mounted read-write at /raw-data (read-write, not :ro,
# so a WAL-mode source's -shm/-wal can be opened normally) and the archive
# directory is bind-mounted so the output path ($1) is writable.
#
# Usage: sh sqlite-archive.sh <output-archive-path.tar.gz>
#
# Exits non-zero (failing the volume's backup loudly) if no SQLite database is
# found or if any .backup fails — we never silently fall back to an unsafe raw tar.
set -eu

ARCHIVE_PATH="${1:?usage: sqlite-archive.sh <output-archive-path>}"
RAW=/raw-data
STAGE="$(mktemp -d)"
DBLIST="$(mktemp)"

cleanup() { rm -rf "$STAGE" "$DBLIST"; }
trap cleanup EXIT

cd "$RAW"

# 1. Identify SQLite databases by their header magic ("SQLite format 3\0").
#    Detecting by content (not extension) handles any filename and naturally
#    excludes -wal/-shm/-journal sidecars (which don't carry the magic). Only the
#    leading 15 ASCII bytes are read, so grep never sees the trailing NUL/binary.
find . -type f | while IFS= read -r f; do
	if head -c 15 "$f" 2>/dev/null | grep -qF 'SQLite format 3'; then
		printf '%s\n' "$f" >> "$DBLIST"
	fi
done

if [ ! -s "$DBLIST" ]; then
	echo "sqlite-archive: no SQLite database found in $RAW — refusing to archive (is backup_strategy: sqlite set on a non-SQLite volume?)" >&2
	exit 3
fi

# 2. Snapshot each database with the online backup API into the staging tree,
#    preserving relative paths. A busy timeout tolerates a brief writer lock from
#    the live service. .backup reads through any WAL, so the output is a single,
#    consistent, sidecar-free database file.
while IFS= read -r db; do
	mkdir -p "$STAGE/$(dirname "$db")"
	echo "sqlite-archive: snapshotting $db" >&2
	sqlite3 -cmd '.timeout 10000' "$db" ".backup '$STAGE/$db'"
done < "$DBLIST"

# 3. Copy any non-database files verbatim, so a mixed volume (DB + other files) is
#    archived completely. Skip the databases (snapshotted above) and their
#    sidecars (only meaningful with their live DB; they must not travel alongside
#    a .backup snapshot).
find . -type f | while IFS= read -r f; do
	if grep -qxF "$f" "$DBLIST"; then continue; fi
	case "$f" in
		*-wal|*-shm|*-journal)
			base="${f%-wal}"; base="${base%-shm}"; base="${base%-journal}"
			if grep -qxF "$base" "$DBLIST"; then continue; fi
			;;
	esac
	mkdir -p "$STAGE/$(dirname "$f")"
	cp -a "$f" "$STAGE/$f"
done

# 4. Archive only the staging tree (consistent snapshots + verbatim extras).
tar -C "$STAGE" -czf "$ARCHIVE_PATH" .
echo "sqlite-archive: wrote $ARCHIVE_PATH" >&2
