#!/bin/bash
## Restores a Docker volume from a backup archive.
## The volume is recreated via Docker Compose to ensure correct labels are applied,
## which prevents lucos_backups tracking failures caused by missing Compose labels.
##
## *** AD-HOC USE ONLY ***
## This script is run manually by an admin during a volume restore incident.
## It is NOT invoked by the lucos_backups application, web server, or any automated process.
##
## Where to run this script:
## This script must run on the production Docker host where the volume lives,
## because it interacts directly with the local Docker daemon (docker, docker compose).
## Production Docker hosts do not persistently store the source repositories or
## docker-compose.yml files — this script fetches the relevant compose file from
## GitHub automatically if it is not available locally.
##
## Getting the script onto the production host:
## Option A — fetch directly on the host:
##   wget -O /tmp/restore-volume.sh \
##     https://raw.githubusercontent.com/lucas42/lucos_backups/main/restore-volume.sh
##   chmod +x /tmp/restore-volume.sh
##   bash /tmp/restore-volume.sh <volume_name> <archive_path>
##
## Option B — copy from your local machine:
##   scp restore-volume.sh <user>@<host>:/tmp/restore-volume.sh
##   ssh <user>@<host> bash /tmp/restore-volume.sh <volume_name> <archive_path>
##
## For a full restore runbook including volume-specific notes and verification steps,
## see docs/restore-runbook.md in this repository.
##
## Usage: ./restore-volume.sh <volume_name> <archive_path> [compose_dir]
##
##   volume_name  - The Docker volume to restore (e.g. lucos_photos_postgres_data)
##   archive_path - Path to the .tar.gz archive to restore from
##   compose_dir  - (Optional) Directory containing docker-compose.yml.
##                  If omitted, fetched from GitHub (lucas42/<project> on main).
##
set -euo pipefail

VOLUME_NAME="${1:-}"
ARCHIVE_PATH="${2:-}"
COMPOSE_DIR="${3:-}"

# --- Argument validation ---
if [ -z "$VOLUME_NAME" ] || [ -z "$ARCHIVE_PATH" ]; then
	echo "Usage: $0 <volume_name> <archive_path> [compose_dir]"
	echo ""
	echo "  volume_name  - Docker volume to restore (e.g. lucos_photos_postgres_data)"
	echo "  archive_path - Path to the .tar.gz archive to restore from"
	echo "  compose_dir  - (Optional) Directory containing docker-compose.yml"
	echo "                 Fetched from GitHub (lucas42/<project>/main) if omitted"
	exit 1
fi

# Validate archive exists and is non-empty before doing anything destructive
if [ ! -f "$ARCHIVE_PATH" ]; then
	echo "Error: Archive file not found: $ARCHIVE_PATH"
	exit 1
fi
if [ ! -s "$ARCHIVE_PATH" ]; then
	echo "Error: Archive file is empty: $ARCHIVE_PATH"
	exit 1
fi

# --- Find compose directory ---
if [ -z "$COMPOSE_DIR" ]; then
	PROJECT_NAME=""

	# Try to get project name from existing volume labels
	if docker volume inspect "$VOLUME_NAME" &>/dev/null; then
		PROJECT_NAME=$(docker volume inspect "$VOLUME_NAME" --format '{{ index .Labels "com.docker.compose.project" }}' 2>/dev/null || true)
	fi

	# If no label found, derive from volume name by trying progressively shorter
	# prefixes (lucos convention: volume name = project_volumename)
	if [ -z "$PROJECT_NAME" ]; then
		PREFIX="$VOLUME_NAME"
		while true; do
			# Check for a local copy first (e.g. on a dev machine with repos checked out)
			if [ -f "/srv/$PREFIX/docker-compose.yml" ]; then
				PROJECT_NAME="$PREFIX"
				COMPOSE_DIR="/srv/$PREFIX"
				break
			fi
			# Remove the last underscore-separated segment and try again
			NEW_PREFIX="${PREFIX%_*}"
			if [ "$NEW_PREFIX" = "$PREFIX" ]; then
				# No more underscores to remove — exhausted all options
				break
			fi
			PREFIX="$NEW_PREFIX"
		done
	fi

	if [ -z "$PROJECT_NAME" ]; then
		echo "Error: Could not determine Docker Compose project for volume '$VOLUME_NAME'."
		echo ""
		echo "Either:"
		echo "  - The volume has no Docker Compose labels (it may have been created outside Compose)"
		echo "  - No matching docker-compose.yml found locally"
		echo ""
		echo "Please specify the compose directory explicitly:"
		echo "  $0 $VOLUME_NAME $ARCHIVE_PATH <compose_dir>"
		exit 1
	fi
fi

# --- Get docker-compose.yml if not already available locally ---
TEMP_COMPOSE_DIR=""
if [ -z "$COMPOSE_DIR" ] || [ ! -f "$COMPOSE_DIR/docker-compose.yml" ]; then
	# Production hosts do not persistently store docker-compose.yml files —
	# fetch it from GitHub for the relevant project.
	echo "docker-compose.yml not found locally — fetching from GitHub..."
	TEMP_COMPOSE_DIR=$(mktemp -d)
	GITHUB_URL="https://raw.githubusercontent.com/lucas42/${PROJECT_NAME}/main/docker-compose.yml"
	if command -v curl &>/dev/null; then
		curl -sf -o "${TEMP_COMPOSE_DIR}/docker-compose.yml" "$GITHUB_URL"
	elif command -v wget &>/dev/null; then
		wget -q -O "${TEMP_COMPOSE_DIR}/docker-compose.yml" "$GITHUB_URL"
	else
		echo "Error: Neither curl nor wget is available to fetch docker-compose.yml."
		echo "Please copy docker-compose.yml for '$PROJECT_NAME' to a local directory and"
		echo "pass it as the third argument: $0 $VOLUME_NAME $ARCHIVE_PATH <compose_dir>"
		exit 1
	fi
	if [ ! -s "${TEMP_COMPOSE_DIR}/docker-compose.yml" ]; then
		echo "Error: Failed to fetch docker-compose.yml from $GITHUB_URL"
		echo "Please specify the compose directory explicitly:"
		echo "  $0 $VOLUME_NAME $ARCHIVE_PATH <compose_dir>"
		rm -rf "$TEMP_COMPOSE_DIR"
		exit 1
	fi
	COMPOSE_DIR="$TEMP_COMPOSE_DIR"
	echo "Fetched docker-compose.yml for '$PROJECT_NAME'."
fi

# Get archive size for the summary
ARCHIVE_SIZE=$(du -sh "$ARCHIVE_PATH" | cut -f1)

# --- Print summary and ask for confirmation ---
echo ""
echo "=== Volume Restore Summary ==="
echo "  Volume:      $VOLUME_NAME"
echo "  Archive:     $ARCHIVE_PATH ($ARCHIVE_SIZE)"
echo "  Compose dir: $COMPOSE_DIR"
echo ""
echo "This will:"
echo "  1. Stop all running containers that use '$VOLUME_NAME'"
echo "  2. Delete the existing volume (permanently)"
echo "  3. Recreate it via Docker Compose (to apply correct Compose labels)"
echo "  4. Restore data from the archive"
echo ""
echo "WARNING: Any existing data in the volume will be PERMANENTLY DELETED."
echo ""
read -r -p "Type 'yes' to proceed: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
	echo "Aborted."
	[ -n "$TEMP_COMPOSE_DIR" ] && rm -rf "$TEMP_COMPOSE_DIR"
	exit 0
fi

echo ""

# --- Stop containers using this volume ---
echo "Stopping containers using volume '$VOLUME_NAME'..."
CONTAINERS=$(docker ps --filter volume="$VOLUME_NAME" --format "{{.ID}}" 2>/dev/null || true)
if [ -n "$CONTAINERS" ]; then
	echo "$CONTAINERS" | xargs docker stop
	echo "Containers stopped."
else
	echo "No running containers found using this volume."
fi

# --- Delete existing volume ---
if docker volume inspect "$VOLUME_NAME" &>/dev/null; then
	echo "Removing existing volume '$VOLUME_NAME'..."
	docker volume rm "$VOLUME_NAME"
	echo "Volume removed."
else
	echo "Volume '$VOLUME_NAME' does not exist yet (will be created fresh)."
fi

# --- Recreate volume via Docker Compose (applies correct labels) ---
echo "Recreating volume via Docker Compose..."
(cd "$COMPOSE_DIR" && docker compose up --no-start 2>&1)
echo "Volume created with Docker Compose labels."

# Verify labels were applied
COMPOSE_PROJECT=$(docker volume inspect "$VOLUME_NAME" --format '{{ index .Labels "com.docker.compose.project" }}' 2>/dev/null || true)
if [ -z "$COMPOSE_PROJECT" ]; then
	echo ""
	echo "Warning: Volume '$VOLUME_NAME' was not created by the Docker Compose step."
	echo "It may not be defined in the docker-compose.yml for this project."
	echo "The restore will continue but the volume may lack Compose labels."
	echo ""
fi

# --- Restore data from archive ---
echo "Restoring data from archive..."
ARCHIVE_DIR=$(dirname "$(realpath "$ARCHIVE_PATH")")
ARCHIVE_FILE=$(basename "$ARCHIVE_PATH")
docker run --rm \
	--volume "${VOLUME_NAME}:/raw-data" \
	--mount "src=${ARCHIVE_DIR},target=${ARCHIVE_DIR},type=bind" \
	alpine:latest \
	tar -C /raw-data -xzf "${ARCHIVE_DIR}/${ARCHIVE_FILE}"

# Clean up any temporary compose dir
[ -n "$TEMP_COMPOSE_DIR" ] && rm -rf "$TEMP_COMPOSE_DIR"

echo ""
echo "=== Restore Complete ==="
echo "Volume '$VOLUME_NAME' restored from '$ARCHIVE_PATH'."
echo ""
echo "Next steps:"
echo "  - Restart services: docker compose -f <project-compose-file> up -d"
echo "  - Verify the restored data looks correct before resuming normal operations"
echo "  - See docs/restore-runbook.md in lucos_backups for volume-specific verification steps"
