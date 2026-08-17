FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Shell form (not exec-form JSON array) so $PORT actually expands — Railway
# injects its own port at runtime and routes traffic there, so a hardcoded
# --port would silently break the deployment. Falls back to 8000 for local
# docker-compose, where no PORT env var is set.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 4
