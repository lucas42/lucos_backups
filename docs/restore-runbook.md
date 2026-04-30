# Volume Restore Runbook

This runbook covers how to restore Docker volumes from lucos_backups archives.

---

## ⚠️ Critical: Docker Compose Labels

**Volumes restored outside Docker Compose will be missing their Compose labels.** This causes `lucos_backups` to crash when it tries to track the volume:

```
Exception: No Docker Compose project label on volume lucos_photos_postgres_data
```

Docker Compose applies three labels to volumes it manages:

| Label | Example value |
|---|---|
| `com.docker.compose.project` | `lucos_photos` |
| `com.docker.compose.version` | `2.24.0` |
| `com.docker.compose.volume` | `postgres_data` |

A bare `docker volume create` or `docker run --volume` does not apply these labels. **Always use the `restore-volume.sh` script or the manual equivalent below** — both recreate the volume through Docker Compose before populating it.

To check whether an existing volume has labels:

```sh
docker volume inspect <volume_name> --format '{{ .Labels }}'
```

An empty result (`map[]`) means labels are missing.

---

## Backup Archive Location

Archives are stored in two places:

| Location | Path |
|---|---|
| On the originating host | `/srv/backups/local/volume/<volume_name>.<date>.tar.gz` |
| On each backup host | `/srv/backups/host/<originating_host>/volume/<volume_name>.<date>.tar.gz` |

Choose the most recent archive from a date before the data loss occurred. Prefer archives from the originating host where available — they're fresher.

---

## General Restore Procedure

### Recommended: use `restore-volume.sh`

The `restore-volume.sh` script in the root of this repo handles everything automatically:

```sh
./restore-volume.sh <volume_name> <archive_path>
```

Example:

```sh
./restore-volume.sh lucos_photos_postgres_data \
  /srv/backups/local/volume/lucos_photos_postgres_data.2026-03-15.tar.gz
```

The script:
1. Validates the archive exists and is non-empty
2. Auto-detects the Docker Compose directory
3. Shows a summary and asks for `yes` confirmation
4. Stops containers using the volume
5. Deletes the existing volume and recreates it via `docker compose up --no-start` (applying correct labels)
6. Restores data from the archive

After the script completes, restart the service and verify (see [volume-specific sections](#volume-specific-notes) below).

> **Note:** When `docker-compose.yml` is not available locally (the usual production case), the script fetches it automatically from GitHub (`raw.githubusercontent.com`). This auto-fetch path has two hard dependencies:
>
> 1. **Network access** from the production host to GitHub (`raw.githubusercontent.com`)
> 2. **A pullable Docker image** — `docker compose up --no-start` must resolve the service's image to create the container and apply Compose labels to the volume. If the image isn't cached locally and can't be pulled, the volume recreation step fails.
>
> In a severe incident (network outage, fresh host rebuild, or registry unreachable), both of these may be unavailable. If the script fails at the fetch or volume-creation step, fall back to the manual procedure below and create the volume with `docker volume create --label ...` to apply the Compose labels by hand.

### Manual equivalent

If you need to do it by hand:

```sh
# 1. Stop containers using the volume
docker stop $(docker ps --filter volume=<volume_name> --format "{{.ID}}")

# 2. Delete the existing volume
docker volume rm <volume_name>

# 3. Recreate via Docker Compose (applies correct labels)
cd /srv/<project_name>
docker compose up --no-start

# 4. Restore data from archive
ARCHIVE_DIR=$(dirname <archive_path>)
ARCHIVE_FILE=$(basename <archive_path>)
docker run --rm \
  --volume <volume_name>:/raw-data \
  --mount src=${ARCHIVE_DIR},target=${ARCHIVE_DIR},type=bind \
  alpine:latest \
  tar -C /raw-data -xzf ${ARCHIVE_DIR}/${ARCHIVE_FILE}

# 5. Restart the service
docker compose up -d
```

---

## Volume-Specific Notes

### PostgreSQL databases

**Volumes:** `lucos_photos_postgres_data`, `lucos_contacts_db_data`, `lucos_eolas_db_data`

**Restore:**

```sh
./restore-volume.sh <volume_name> <archive_path>
cd /srv/<project_name> && docker compose up -d
```

**Verify:** Check the database is accepting connections and data looks intact:

```sh
# For lucos_photos (user=photos, db=photos, container=lucos_photos_postgres)
docker exec lucos_photos_postgres psql -U photos -c '\dt'
docker exec lucos_photos_postgres psql -U photos -d photos -c 'SELECT COUNT(*) FROM media_item;'

# For lucos_contacts (default postgres user, container=lucos_contacts_db)
docker exec lucos_contacts_db psql -U postgres -c '\dt'

# For lucos_eolas (default postgres user, db=postgres, container=lucos_eolas_db)
docker exec lucos_eolas_db psql -U postgres -d postgres -c '\dt'
```

Also check the service's `/_info` endpoint responds correctly.

**⚠️ Side effects:**

- **Restoring a PostgreSQL backup wipes all data written after the backup date.** This includes telemetry, event logs, and any records created between the backup date and the incident. During the 2026-03-17 incident, restoring `lucos_photos_postgres_data` from a 2026-03-15 backup silently deleted all telemetry from 2026-03-16 — this was later misread as evidence the Android app hadn't run that day. See [lucos_photos#211](https://github.com/lucas42/lucos_photos/issues/211).
- After restoring `lucos_photos_postgres_data`, any photo processing jobs that ran between the backup and the incident will need to be re-queued.

---

### File storage volumes

**Volumes:** `lucos_photos_photos`, `lucos_photos_uploads`, `lucos_notes_stateFile`, `lucos_media_manager_stateFile`, `lucos_media_metadata_api_exports`, `lucos_media_metadata_api_db`

**Restore:**

```sh
./restore-volume.sh <volume_name> <archive_path>
cd /srv/<project_name> && docker compose up -d
```

**Verify:** Check that expected files are present in the volume:

```sh
docker run --rm --volume <volume_name>:/data alpine:latest ls -la /data
```

For `lucos_photos_photos`, confirm a sample of original files and derivatives are present and the API can serve them.

For `lucos_media_metadata_api_db`, the volume contains a SQLite file at `media.sqlite` (not a PostgreSQL database). Confirm it is present and non-empty:

```sh
docker run --rm --volume lucos_media_metadata_api_db:/data alpine:latest ls -lh /data/media.sqlite
```

**Side effects:** Only data created after the backup date is lost. No cross-volume effects.

---

### Small/config/state volumes

**Volumes:** `lucos_authentication_config`, `lucos_dns_generatedzones`, `lucos_router_generatedconfig`, `lucos_router_letsencrypt`, `lucos_loganne_state`, `lucos_locations_config`, `lucos_locations_mosquitto_data`, `lucos_locations_mosquitto_log`, `lucos_repos_data`, `lucos_schedule_tracker_db`, `lucos_creds_store`

**Note on effort levels:** Several of these volumes are classified as `automatic` or `tolerable` effort — meaning data can be regenerated automatically or loss is acceptable. Check `config.yaml` for the `recreate_effort` value before deciding whether to restore from backup or just restart the service and let it rebuild.

For `lucos_creds_store` (credentials): this is `considerable` effort to recreate manually. Always restore from backup rather than trying to recreate.

**Restore:**

```sh
./restore-volume.sh <volume_name> <archive_path>
cd /srv/<project_name> && docker compose up -d
```

**Verify:** Restart the service and confirm it starts cleanly. For generated volumes (`lucos_dns_generatedzones`, `lucos_router_generatedconfig`), trigger a regeneration after restart to ensure the restored data is still valid.

---

### Remote/NAS mounts

**Volumes:** `lucos_media_import_media`, `lucos_private_medlib`, `lucos_static_media_public`

These volumes are NFS or SFTP remote mounts — **they are not backed up by lucos_backups** and do not appear in the restore flow. If they appear missing, check the NAS connection and remount rather than attempting a restore.

---

## Post-Restore Checklist

After any volume restore:

- [ ] Service is running: `docker compose ps` in the project directory
- [ ] Service health: `curl -sf http://127.0.0.1:<PORT>/_info`
- [ ] Volume has Compose labels: `docker volume inspect <volume_name> --format '{{ .Labels }}'`
- [ ] lucos_backups tracking is not erroring on this volume (check the backups UI or trigger a refresh)
- [ ] If a database was restored: verify record counts look plausible, check for any missing data in the affected date range
- [ ] Document what was restored, from which date, and why — in the relevant incident report
