FROM lucas42/lucos_navbar:2.3.2 AS navbar
# Must be a STABLE CPython release. Pre-release tags (3.15.0aN/bN/rcN) build
# fine but break at runtime: binary wheels published for cp3XX are compiled
# against a later pre-release with a wider C-API type-slot table, so importing
# them dies with "unknown slot ID N". This took backups down for 15h on
# 2026-08-17 (see lucas42/lucos_backups#390).
FROM python:3.14.6-alpine AS app
ARG VERSION
ENV VERSION=$VERSION

WORKDIR /usr/src/app

# rsync is used by the incremental backup strategy (ADR-0002): this same image
# is run as a container on the source host to perform `rsync --link-dest`
# snapshots, so the binary ships in the versioned image rather than being
# installed on any host.
RUN apk add sed curl openssh-client rsync
# Pinned: an unpinned pipenv can compute a different Pipfile.lock hash than
# the one committed, causing --deploy below to fail on unrelated commits.
RUN pip install pipenv==2026.7.1

COPY src/backups.cron .
RUN cat backups.cron | crontab -
RUN rm backups.cron
COPY src/*.sh .

COPY src/Pipfile* ./
# --deploy installs strictly from Pipfile.lock and fails the build if the
# lock is out of sync with Pipfile, instead of silently re-resolving from
# PyPI (lucas42/lucos_backups#392).
RUN pipenv install --deploy

COPY src /usr/src/app
COPY --from=navbar lucos_navbar.js resources/

# Runs the suite against the shipped interpreter and dependency set. CI builds
# this target; see .circleci/config.yml.
FROM app AS test
RUN pipenv install --deploy --dev
CMD [ "pipenv", "run", "python", "-m", "pytest", "tests/", "-q"]

# Shipped stage is pinned via docker-compose.yml's build.target: production,
# not by stage order (lucas42/lucos_backups#396).
FROM app AS production
CMD [ "./scripts/startup.sh"]