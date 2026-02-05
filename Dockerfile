FROM lucas42/lucos_navbar:2.1.2 AS navbar
FROM python:3.15.0a5-alpine

WORKDIR /usr/src/app

RUN apk add sed curl openssh-client
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