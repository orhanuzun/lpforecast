FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["gunicorn","-w","2","-k","gthread","--threads","4","-b","0.0.0.0:5000","--capture-output","--access-logfile","-","--error-logfile","-","--log-level","info","Riot:app"]