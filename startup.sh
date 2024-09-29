#!/bin/sh
set -e
printenv > .env
[ -p /var/log/cron.log ] || mkfifo /var/log/cron.log
/usr/sbin/crond
cat <> /var/log/cron.log&

pipenv run python -u server.py