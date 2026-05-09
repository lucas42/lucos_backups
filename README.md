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
* __restore-volume.sh \<volume_name\> \<archive_path\>__ - restores a Docker volume from a backup archive on a production host. Run on the host where the volume lives; see [docs/restore-runbook.md](docs/restore-runbook.md) for full instructions.

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
