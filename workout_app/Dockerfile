# Container for Cloud Run (built by `gcloud run deploy --source workout_app`).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY wsgi.py .
COPY app ./app

# Never run as root inside the container.
RUN useradd --create-home --uid 10001 fitlog
USER fitlog

# Cloud Run sets $PORT. One worker + threads keeps the in-memory rate limiter consistent;
# --timeout 120 leaves room for an AI-built workout.
CMD exec gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 8 --timeout 120
