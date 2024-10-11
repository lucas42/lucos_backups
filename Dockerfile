FROM lucas42/lucos_navbar:latest AS navbar
FROM python:3.13-alpine

WORKDIR /usr/src/app

RUN apk add sed curl
RUN pip install pipenv

COPY src/backups.cron .
RUN cat backups.cron | crontab -
RUN rm backups.cron
COPY src/startup.sh .

COPY src/Pipfile* ./
RUN pipenv install

COPY src/*.py ./
COPY src/*.yaml ./
COPY src/resources resources
COPY src/templates templates
COPY --from=navbar lucos_navbar.js resources/

EXPOSE $PORT
CMD [ "./startup.sh"]