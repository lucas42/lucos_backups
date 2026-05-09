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

if ssh -T $HOSTNAME "id -u \"$USERNAME\" >/dev/null 2>&1"; then
	echo "User $USERNAME already exists"
else
	echo "Creating user $USERNAME"
	ssh -T $HOSTNAME "sudo useradd --system --create-home $USERNAME"
fi

echo "Adding $USERNAME to docker group"
if ssh -T $HOSTNAME "command -v usermod >/dev/null 2>&1"; then
	ssh -T $HOSTNAME "sudo usermod -G docker $USERNAME"
else
	ssh -T $HOSTNAME "sudo addgroup $USERNAME docker"
fi

dailyuser=`ssh -T $HOSTNAME whoami`
echo "Adding ${dailyuser} to ${USERNAME} group"
if ssh -T $HOSTNAME "command -v usermod >/dev/null 2>&1"; then
	ssh -T $HOSTNAME "sudo usermod -a -G ${USERNAME} ${dailyuser}"
else
	ssh -T $HOSTNAME "sudo addgroup ${dailyuser} ${USERNAME}"
fi

echo "Creating .ssh directory"
ssh -T $HOSTNAME "sudo mkdir -p ~${USERNAME}/.ssh && sudo chown $USERNAME ~${USERNAME}/.ssh && sudo chmod 700 ~${USERNAME}/.ssh"

echo "Writing public SSH key"
./update-authorized-keys.sh "$HOSTNAME"

echo "Creating directory /srv/backups"
ssh -T $HOSTNAME "sudo mkdir -p /srv/backups && sudo chown $USERNAME /srv/backups"

echo "Done"
