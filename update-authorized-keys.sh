## Pushes the current SSH public key to every backup host's authorized_keys.
## Run this after rotate-ssh-key.sh, or any time a host's authorized_keys may be stale.
## This script should be run manually by a user which has:
## * SSH access to all backup hosts (aurora, avalon, salvare, xwing)
## * Sudo permissions on each backup host
## * An SSH key trusted by the lucos_creds service
##
#!/bin/sh
set -e

USERNAME="lucos-backups"
HOSTS="aurora avalon salvare xwing"

# Get production credentials from lucos_creds service
rm -rf update.env
scp -P 2202 "creds.l42.eu:lucos_backups/production/.env" update.env
source update.env
rm update.env

for HOST in $HOSTS; do
	echo "Updating authorized_keys on $HOST..."
	ssh -T $HOST "echo \"$SSH_PUBLIC_KEY\" | sudo tee /home/${USERNAME}/.ssh/authorized_keys >/dev/null"
	echo "Done on $HOST"
done

echo ""
echo "Testing login with new key on each host..."
ssh-add - <<< "$SSH_PRIVATE_KEY"
for HOST in $HOSTS; do
	echo "Testing $HOST..."
	ssh ${USERNAME}@${HOST} echo "Successful Login on $HOST"
done
ssh-add -D

echo ""
echo "All hosts updated and verified"
