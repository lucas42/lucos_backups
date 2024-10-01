## Generates a new ssh key for use by the lucos-backups user and stores it in lucos_creds
## This script should be run manually by a user which has:
## * Write access to the local filesystem (files are temporary and get tidied up by script)
## * An SSH key trusted by the lucos_creds service
##
#!/bin/sh
set -e

rm -f new-ssh-key*
ssh-keygen -t ed25519 -C lucos_backups -N "" -f new-ssh-key -q <<< "y" > /dev/null
PRIVATE_KEY=`cat new-ssh-key | sed 's/=/~/g'` # HACK: Replace padding characters with tildas because lucos_creds gets confused by equal signs
PUBLIC_KEY=`cat new-ssh-key.pub`
rm -f new-ssh-key*

# Store public and private keys in production and development environments
ssh -p 2202 creds.l42.eu "lucos_backups/production/SSH_PRIVATE_KEY=$PRIVATE_KEY"
ssh -p 2202 creds.l42.eu "lucos_backups/production/SSH_PUBLIC_KEY=$PUBLIC_KEY"
ssh -p 2202 creds.l42.eu "lucos_backups/development/SSH_PRIVATE_KEY=$PRIVATE_KEY"
ssh -p 2202 creds.l42.eu "lucos_backups/development/SSH_PUBLIC_KEY=$PUBLIC_KEY"

echo "Key updated in lucos_creds"