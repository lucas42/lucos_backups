## Restores a Docker volume from a backup archive.
## The volume is recreated via Docker Compose to ensure correct labels are applied,
## which prevents lucos_backups tracking failures caused by missing Compose labels.
##
## Usage: ./restore-volume.sh <volume_name> <archive_path> [compose_dir]
##
##   volume_name  - The Docker volume to restore (e.g. lucos_photos_postgres_data)
##   archive_path - Path to the .tar.gz archive to restore from
##   compose_dir  - (Optional) Directory containing docker-compose.yml.
##                  If omitted, auto-detected from volume labels or /srv/ convention.
##
#!/bin/bash
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
	echo "                 Auto-detected from volume labels or /srv/ convention if omitted"
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
	# prefixes against /srv/ (lucos convention: service lives at /srv/{project}/)
	if [ -z "$PROJECT_NAME" ]; then
		PREFIX="$VOLUME_NAME"
		while true; do
			if [ -f "/srv/$PREFIX/docker-compose.yml" ]; then
				PROJECT_NAME="$PREFIX"
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
		echo "  - No matching docker-compose.yml found under /srv/"
		echo ""
		echo "Please specify the compose directory explicitly:"
		echo "  $0 $VOLUME_NAME $ARCHIVE_PATH <compose_dir>"
		exit 1
	fi

	COMPOSE_DIR="/srv/$PROJECT_NAME"
fi

# Validate compose dir contains a docker-compose.yml
if [ ! -f "$COMPOSE_DIR/docker-compose.yml" ]; then
	echo "Error: No docker-compose.yml found in: $COMPOSE_DIR"
	exit 1
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
	echo "It may not be defined in $COMPOSE_DIR/docker-compose.yml."
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

echo ""
echo "=== Restore Complete ==="
echo "Volume '$VOLUME_NAME' restored from '$ARCHIVE_PATH'."
echo ""
echo "Next steps:"
echo "  - Restart services: cd $COMPOSE_DIR && docker compose up -d"
echo "  - Verify the restored data looks correct before resuming normal operations"
