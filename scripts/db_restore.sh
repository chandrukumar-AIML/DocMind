#!/bin/sh
# db_restore.sh — Restore a DocMind PostgreSQL backup
#
# Usage:
#   ./scripts/db_restore.sh <backup_file.sql.gz>
#   ./scripts/db_restore.sh s3://your-bucket/backups/postgres/documind_backup_20260710_120000.sql.gz
#
# WARNING: This drops and recreates the target database. Run against staging first.

set -e

BACKUP_ARG="$1"
if [ -z "$BACKUP_ARG" ]; then
  echo "Usage: $0 <backup_file.sql.gz | s3://...>"
  exit 1
fi

if [ -z "$DATABASE_URL" ]; then
  echo "ERROR: DATABASE_URL is not set"
  exit 1
fi

# Determine local file path
if echo "$BACKUP_ARG" | grep -q "^s3://"; then
  LOCAL_FILE="/tmp/restore_$(date -u +%s).sql.gz"
  echo "[restore] Downloading ${BACKUP_ARG} from S3..."
  aws s3 cp "$BACKUP_ARG" "$LOCAL_FILE"
else
  LOCAL_FILE="$BACKUP_ARG"
fi

if [ ! -f "$LOCAL_FILE" ]; then
  echo "[restore] ERROR: Backup file not found: ${LOCAL_FILE}"
  exit 1
fi

# Parse DATABASE_URL
DB_URL="$DATABASE_URL"
DB_URL="${DB_URL#postgresql://}"
DB_URL="${DB_URL#postgres://}"
DB_USER="${DB_URL%%:*}"
DB_URL="${DB_URL#*:}"
DB_PASS="${DB_URL%%@*}"
DB_URL="${DB_URL#*@}"
DB_HOST="${DB_URL%%:*}"
DB_URL="${DB_URL#*:}"
DB_PORT="${DB_URL%%/*}"
DB_NAME="${DB_URL#*/}"
DB_NAME="${DB_NAME%%\?*}"

echo "[restore] Target: ${DB_NAME} @ ${DB_HOST}:${DB_PORT}"
echo "[restore] Source: ${LOCAL_FILE}"
echo ""
echo "WARNING: This will DROP and recreate the database '${DB_NAME}'."
echo "Press Ctrl+C within 10 seconds to abort..."
sleep 10

export PGPASSWORD="$DB_PASS"

# Drop + recreate (connect to postgres maintenance DB)
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres <<SQL
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "${DB_NAME}";
CREATE DATABASE "${DB_NAME}" OWNER "${DB_USER}";
SQL

echo "[restore] Database recreated. Restoring backup..."

gunzip -c "$LOCAL_FILE" | psql \
  -h "$DB_HOST" \
  -p "$DB_PORT" \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  --single-transaction \
  --quiet

echo "[restore] Restore complete."

# Run Alembic migrations to bring schema up to head
if command -v alembic > /dev/null 2>&1; then
  echo "[restore] Running Alembic migrations to head..."
  alembic upgrade head
  echo "[restore] Migrations applied."
fi

echo "[restore] Done."
