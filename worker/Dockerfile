FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Reuse backend deps — the worker is just a long-running consumer of the same code.
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Bring in the backend code (sandbox runner, models, queue helpers, etc.).
COPY backend/app /app/app
COPY worker/app /app/worker_app

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "worker_app.worker"]