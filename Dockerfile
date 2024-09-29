FROM python:3.12-alpine

WORKDIR /usr/src/app

RUN pip install pipenv

RUN echo "25 3 * * * cd `pwd` && pipenv run python -u do-backups.py >> /var/log/cron.log 2>&1" | crontab -
COPY startup.sh .

COPY Pipfile* ./
RUN pipenv install

COPY src/* ./

EXPOSE $PORT
CMD [ "./startup.sh"]