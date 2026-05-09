#!/bin/sh
set -e

[ -n "$SSH_AUTH_SOCK" ] || eval `ssh-agent -s`
echo "$SSH_PRIVATE_KEY" | ssh-add -