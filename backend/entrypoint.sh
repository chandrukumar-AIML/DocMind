#!/bin/sh
# entrypoint.sh — run Alembic migrations then start the server.
set -e

VENV_BIN="/opt/venv/bin"
ALEMBIC="${VENV_BIN}/alembic"
UVICORN="${VENV_BIN}/uvicorn"

# Run migrations with retry — DB may need a moment to accept connections.
# If tables already exist without an alembic_version row (fresh stamp needed),
# stamp head so future incremental migrations work correctly.
echo "[entrypoint] Running database migrations..."
MIGRATION_OK=0
for attempt in 1 2 3 4 5; do
    ALEMBIC_OUT=$("$ALEMBIC" upgrade head 2>&1)
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ]; then
        MIGRATION_OK=1
        echo "[entrypoint] Migrations complete (attempt ${attempt})."
        break
    fi
    # Tables exist but alembic has no version record — stamp head and retry once
    if echo "$ALEMBIC_OUT" | grep -q "already exists\|DuplicateTable"; then
        echo "[entrypoint] Tables already exist without alembic history — stamping head..."
        "$ALEMBIC" stamp head && MIGRATION_OK=1 && echo "[entrypoint] Stamped head." && break
    fi
    echo "[entrypoint] Migration attempt ${attempt}/5 failed — retrying in 10s..."
    echo "$ALEMBIC_OUT" | tail -3
    sleep 10
done

if [ "$MIGRATION_OK" = "0" ]; then
    echo "[entrypoint] WARNING: All migration attempts failed — starting anyway."
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
