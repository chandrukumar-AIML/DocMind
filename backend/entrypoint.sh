#!/bin/sh
# entrypoint.sh — run Alembic migrations then start the server.

VENV_BIN="/opt/venv/bin"
ALEMBIC="${VENV_BIN}/alembic"
UVICORN="${VENV_BIN}/uvicorn"

# ---------------------------------------------------------------------------
# Migrations — retry up to 5x with backoff.
# NOTE: do NOT use `set -e` here; we need to inspect failures ourselves.
# `if/else` blocks are used instead of capturing exit codes so the shell
# never auto-exits on a non-zero return.
# ---------------------------------------------------------------------------
echo "[entrypoint] Running database migrations..."
MIGRATION_OK=0
attempt=0

while [ $attempt -lt 5 ]; do
    attempt=$((attempt + 1))

    if timeout 30 "$ALEMBIC" upgrade head > /tmp/alembic_out.txt 2>&1; then
        MIGRATION_OK=1
        echo "[entrypoint] Migrations complete (attempt ${attempt})."
        break
    fi

    # Check if tables already exist without alembic history → stamp head
    if grep -q "already exists\|DuplicateTable" /tmp/alembic_out.txt 2>/dev/null; then
        echo "[entrypoint] Tables exist without alembic history — stamping head..."
        if timeout 30 "$ALEMBIC" stamp head >> /tmp/alembic_out.txt 2>&1; then
            MIGRATION_OK=1
            echo "[entrypoint] Stamped head. Migrations will run incrementally from now on."
            break
        fi
    fi

    echo "[entrypoint] Migration attempt ${attempt}/5 failed:"
    tail -3 /tmp/alembic_out.txt
    if [ $attempt -lt 5 ]; then
        echo "[entrypoint] Retrying in 10s..."
        sleep 10
    fi
done

if [ "$MIGRATION_OK" = "0" ]; then
    echo "[entrypoint] WARNING: all migration attempts failed — starting server anyway."
fi

# ---------------------------------------------------------------------------
# Start server — use exec so uvicorn becomes PID 1 and receives signals.
# ---------------------------------------------------------------------------
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
