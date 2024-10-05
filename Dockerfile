FROM lucas42/lucos_navbar:latest AS navbar
FROM python:3.12-alpine

WORKDIR /usr/src/app

RUN apk add sed
RUN pip install pipenv

RUN echo "25 3 * * * cd `pwd` && pipenv run python -u do-backups.py >> /var/log/cron.log 2>&1" | crontab -
COPY src/startup.sh .

COPY src/Pipfile* ./
RUN pipenv install

COPY src/*.py ./
COPY src/*.yaml ./
COPY src/resources resources
COPY --from=navbar lucos_navbar.js resources/

EXPOSE $PORT
CMD [ "./startup.sh"]