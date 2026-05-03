#!/usr/bin/env docker
# Dockerfile for 1c-oData-skill Telegram Bot
# Usage: docker compose up --build

FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user for security
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /sbin/nologin appuser

# Create persistent directories
RUN mkdir -p .cache logs && chown -R appuser:appgroup .cache logs

USER appuser

CMD ["python", "-m", "bot"]