# Flight Watch API image: gunicorn serving the Flask app on port 8080.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY run.py .

# Run as a non-root user. instance/ holds the SQLite file when DATABASE_URL is
# not set (local runs only; on GKE DATABASE_URL points at Cloud SQL).
RUN useradd --create-home appuser && mkdir instance && chown appuser instance
USER appuser

EXPOSE 8080
# 2 workers x 4 threads per pod; scale out with more pods (HPA), not more workers.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 30 --access-logfile - run:app"]
