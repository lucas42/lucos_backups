#!/bin/sh
set -e

[ -n "$SSH_AUTH_SOCK" ] || eval `ssh-agent -s`
echo "$SSH_PRIVATE_KEY" | sed 's/~/=/g' | ssh-add - # Padding characters are stored as tildas due to limitation in lucos_creds