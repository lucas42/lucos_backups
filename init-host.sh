## Sets up a new host for running backups of
## This script should be run manually by a user which has:
## * SSH access to the give host
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

# First, check host exists, is accessible and current user has sudo permissions on it.
ssh -T $HOSTNAME "sudo true"

# Get production credentials from lucos_creds service
rm -rf init.env
scp -P 2202 "creds.l42.eu:lucos_backups/production/.env" init.env
source init.env
rm init.env


if ssh -T $HOSTNAME "id -u \"$USERNAME\" >/dev/null 2>&1"; then
	echo "User $USERNAME already exists"
else
	echo "Creating user $USERNAME"
	ssh -T $HOSTNAME "sudo useradd --system --create-home $USERNAME"
fi
echo "Adding $USERNAME to docker group"
ssh -T $HOSTNAME "sudo usermod -G docker $USERNAME"

echo "Saving public SSH key"
ssh -T $HOSTNAME "sudo mkdir -p /home/${USERNAME}/.ssh && sudo chown $USERNAME /home/${USERNAME}/.ssh && sudo chmod 700 /home/${USERNAME}/.ssh"
ssh -T $HOSTNAME "sudo touch /home/${USERNAME}/.ssh/authorized_keys && sudo chown $USERNAME /home/${USERNAME}/.ssh/authorized_keys && sudo chmod 700 /home/${USERNAME}/.ssh/authorized_keys"
ssh -T $HOSTNAME "echo \"$SSH_PUBLIC_KEY\" | sudo tee /home/${USERNAME}/.ssh/authorized_keys >/dev/null"


echo "Creating directory /srv/backups"
ssh -T $HOSTNAME "sudo mkdir -p /srv/backups && sudo chown $USERNAME /srv/backups"

echo "Testing login"
ssh-add - <<< "${SSH_PRIVATE_KEY/\~/=}"
ssh $USERNAME@$HOSTNAME echo "Successful Login"
ssh-add -D

echo "Done"