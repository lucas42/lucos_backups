# lucos_backups
Backs up files and tracks backups

## Dependencies

* docker
* docker compose

## Setup

The following scripts are to be run manually by a privleged user:
* __rotate-ssh-key.sh__ - generates an SSH public/private key pair for use by the backup user.  Gets stored in lucos_creds.
* __init-host.sh <hostname>__ - sets up a host so the backups service can interact with it.

## Running

`docker compose up --build`

## Refresh tracking data

Tracking data is refreshed on an hourly cronjob.
To manually trigger it, click the 🔃 button in the bottom right of the home page.

## Trigger backups

The backup script is run on a daily cronjob.
To manually trigger it, the following needs to run inside the container:
`source init-agent.sh && pipenv run python -u do-backups.py`