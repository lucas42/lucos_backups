# lucos_backups
Backs up files and tracks backups

## Dependencies

* docker
* docker compose

## Setup

The following scripts are to be run manually by a privileged user:
* __rotate-ssh-key.sh__ - generates a new SSH public/private key pair for the backup user and stores it in lucos_creds. After running this, use `update-authorized-keys.sh` on each backup host to distribute the new key.
* __update-authorized-keys.sh \<hostname\>__ - pushes the current public key from lucos_creds to the given host's `authorized_keys` and verifies the new key works. Run this on each backup host after `rotate-ssh-key.sh`, or any time a host's key gets out of sync. Use `[user@]hostname` format as needed (e.g. `lucas42@aurora.local`).
* __init-host.sh \<hostname\>__ - first-time setup for a new host: creates the `lucos-backups` user, sets group memberships, creates the `.ssh` directory, writes the initial `authorized_keys`, and creates `/srv/backups`. Run once per host, ever. **Do not re-run on existing hosts** — use `update-authorized-keys.sh` instead to refresh keys.
* __restore-volume.sh \<volume_name\> \<archive_path\>__ - restores a Docker volume from a backup archive (or snapshot directory) on a production host. Run on the host where the volume lives; see [docs/restore-runbook.md](docs/restore-runbook.md) for full instructions.
* __scripts/seed-volume.py \<volume_name\>__ - runs the first (full) snapshot of an `incremental`-strategy volume off the nightly cron critical path. Run inside the container: `pipenv run python -m scripts.seed-volume <volume_name>`. See [docs/adr/0002-incremental-photos-backup.md](docs/adr/0002-incremental-photos-backup.md).

## Backup strategies

Each volume's backup mechanism is chosen per-volume via the `backup_strategy` field in [lucos_configy](https://github.com/lucas42/lucos_configy)'s `volumes.yaml`:

* __full-snapshot__ (default, also when the field is absent) — a daily full `tar -czf` archived locally and copied to each backup host. Fine for the small DB-dump / config / state volumes that make up most of the estate.
* __incremental__ — `rsync --link-dest` hardlink-rotated snapshots, for large, append-mostly media volumes where a daily full would exceed the copy window or destination headroom. Source-side `rsync` ships in this repo's container image (run as a container on the source host); each nightly run transfers only that day's deltas into a fresh dated snapshot directory. Decided in [ADR-0002](docs/adr/0002-incremental-photos-backup.md); currently used only by `lucos_photos_photos`.

## Restoring a Volume

See [docs/restore-runbook.md](docs/restore-runbook.md) for the full restore runbook, including volume-specific notes and post-restore verification steps.

## Running

`docker compose up --build`

## Refresh tracking data

Tracking data is refreshed on an hourly cronjob.
To manually trigger it, click the 🔃 button in the bottom right of the home page.

## Trigger backups

The backup script is run on a daily cronjob.
To manually trigger it, the following needs to run inside the container:
`source init-agent.sh && pipenv run python -u do-backups.py`
