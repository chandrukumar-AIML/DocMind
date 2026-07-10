#!/bin/sh
# entrypoint.sh — run Alembic migrations then start the server.
set -e

VENV_BIN="/opt/venv/bin"
ALEMBIC="${VENV_BIN}/alembic"
UVICORN="${VENV_BIN}/uvicorn"

# Run migrations with retry — DB may need a moment to accept connections
# (Supabase free tier wakes up, Render internal networking, etc.)
echo "[entrypoint] Running database migrations..."
MIGRATION_OK=0
for attempt in 1 2 3 4 5; do
    if "$ALEMBIC" upgrade head; then
        MIGRATION_OK=1
        echo "[entrypoint] Migrations complete (attempt ${attempt})."
        break
    fi
    echo "[entrypoint] Migration attempt ${attempt}/5 failed — retrying in 10s..."
    sleep 10
done

if [ "$MIGRATION_OK" = "0" ]; then
    echo "[entrypoint] WARNING: All migration attempts failed. The app will start"
    echo "[entrypoint] anyway — the built-in migration runner will retry on first request."
fi

echo "[entrypoint] Starting DocuMind AI backend..."
exec "$UVICORN" app.main:app \
    --host 0.0.0.0 \
    --port "${API_PORT:-8000}" \
    --workers 1 \
    --loop uvloop \
    --http httptools \
    --log-level info \
    --timeout-keep-alive 300 \
    --timeout-graceful-shutdown 30
