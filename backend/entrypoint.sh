#!/bin/sh
# entrypoint.sh — run Alembic migrations then start the server.
# This ensures schema is always up-to-date before the application accepts traffic.
set -e

echo "[entrypoint] Running database migrations..."
alembic upgrade head
echo "[entrypoint] Migrations complete."

echo "[entrypoint] Starting DocuMind AI backend..."
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${API_PORT:-8000}" \
    --workers 1 \
    --loop uvloop \
    --http httptools \
    --log-level info \
    --timeout-keep-alive 300 \
    --timeout-graceful-shutdown 30
