FROM lucas42/lucos_navbar:2.3.2 AS navbar
# Must be a STABLE CPython release. Pre-release tags (3.15.0aN/bN/rcN) build
# fine but break at runtime: binary wheels published for cp3XX are compiled
# against a later pre-release with a wider C-API type-slot table, so importing
# them dies with "unknown slot ID N". This took backups down for 15h on
# 2026-08-17 (see lucas42/lucos_backups#390).
FROM python:3.14.6-alpine
ARG VERSION
ENV VERSION=$VERSION

WORKDIR /usr/src/app

# rsync is used by the incremental backup strategy (ADR-0002): this same image
# is run as a container on the source host to perform `rsync --link-dest`
# snapshots, so the binary ships in the versioned image rather than being
# installed on any host.
RUN apk add sed curl openssh-client rsync
RUN pip install pipenv

COPY src/backups.cron .
RUN cat backups.cron | crontab -
RUN rm backups.cron
COPY src/*.sh .

COPY src/Pipfile* ./
RUN pipenv install

COPY src /usr/src/app
COPY --from=navbar lucos_navbar.js resources/

CMD [ "./scripts/startup.sh"]