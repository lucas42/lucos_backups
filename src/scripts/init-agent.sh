#!/bin/sh
set -e

if [ -z "$SSH_AUTH_SOCK" ]; then
	eval $(ssh-agent -s)
	trap 'ssh-agent -k' EXIT
fi
echo "$SSH_PRIVATE_KEY" | ssh-add -