# syntax=docker/dockerfile:1.6
# Multi-stage build (builder -> final) for smaller runtime image

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps (gcc & headers for psycopg2-binary wheel fallback / future native deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Leverage layer caching for dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Default config file path (overridden by compose via volume if needed)
ENV ELETTRA_CONFIG_FILE=/app/config/elettra-config.docker.yaml

EXPOSE 8000

# Non-root user (optional)
RUN useradd -m elettra && chown -R elettra /app
USER elettra

# Start server (gunicorn could be used later for multi-worker)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

