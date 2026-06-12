FROM lucas42/lucos_navbar:2.1.73 AS navbar
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