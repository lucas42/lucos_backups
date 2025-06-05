FROM lucas42/lucos_navbar:latest AS navbar
FROM python:3.13.4-alpine

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

EXPOSE $PORT
CMD [ "./scripts/startup.sh"]