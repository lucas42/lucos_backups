# lucos_backups
Backs up files and tracks backups


## Setup

The following scripts are to be run manually by a privleged user:
* __rotate-ssh-key.sh__ - generates an SSH public/private key pair for use by the backup user.  Gets stored in lucos_creds.
* __init-host.sh <hostname>__ - sets up a host so the backups service can interact with it.