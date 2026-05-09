## Pushes the current SSH public key to a backup host's authorized_keys.
## Run this for each backup host after rotate-ssh-key.sh, or any time a host's
## authorized_keys may be stale.
## This script should be run manually by a user which has:
## * SSH access to the given host (as [user@]hostname)
## * Sudo permissions on the given host
## * An SSH key trusted by the lucos_creds service
##
#!/bin/sh
set -e

USERNAME="lucos-backups"
HOSTNAME=$1
if [ -z "$HOSTNAME" ]; then
	echo "Usage: $0 <hostname>"
	exit 1
fi
echo "Running on host $HOSTNAME"

# Get production credentials from lucos_creds service
rm -rf update.env
scp -P 2202 "creds.l42.eu:lucos_backups/production/.env" update.env
source update.env
rm update.env

echo "Saving public SSH key"
ssh -T $HOSTNAME "echo \"$SSH_PUBLIC_KEY\" | sudo tee /home/${USERNAME}/.ssh/authorized_keys >/dev/null"

echo "Testing login"
ssh-add - <<< "$SSH_PRIVATE_KEY"
ssh $USERNAME@$HOSTNAME echo "Successful Login"
ssh-add -D

echo "Done"
