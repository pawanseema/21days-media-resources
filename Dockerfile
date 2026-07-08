FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ api/
COPY config.py gunicorn.conf.py ./
COPY resources/ resources/
COPY search/ search/
COPY ui/ ui/

ENV PORT=8080

CMD ["gunicorn", "-c", "gunicorn.conf.py", "api.flask_api_server:app"]
