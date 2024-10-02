#!/bin/sh
set -e

# printenv doesn't quote values, which is a problem if one contains a space or newline
# So do some hacky regexes to quote stuff
env -0 | sed 's/"/\\"/g' | sed -z "s/\n/\\\\n/g" | sed 's/\x0/\n/g'| sed 's/=/="/' | sed 's/$/"/g' | sed 's/\\n/\n/g' > .env

[ -p /var/log/cron.log ] || mkfifo /var/log/cron.log
/usr/sbin/crond
cat <> /var/log/cron.log&

pipenv run python -u server.py