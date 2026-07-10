#!/bin/sh
# db_backup.sh — PostgreSQL backup with optional S3 upload
#
# Usage:
#   ./scripts/db_backup.sh [--s3]
#
# Required env vars:
#   DATABASE_URL   postgresql://user:pass@host:5432/dbname
#
# Optional env vars (for S3 upload):
#   S3_BUCKET      s3://your-bucket/backups/postgres
#   AWS_REGION     us-east-1
#
# The script creates:
#   /tmp/documind_backup_YYYYMMDD_HHMMSS.sql.gz
# and optionally uploads it to S3.

set -e

TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
BACKUP_FILE="/tmp/documind_backup_${TIMESTAMP}.sql.gz"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

echo "[backup] Starting PostgreSQL backup at ${TIMESTAMP}"

if [ -z "$DATABASE_URL" ]; then
  echo "[backup] ERROR: DATABASE_URL is not set"
  exit 1
fi

# Parse DATABASE_URL into pg_dump args
# Format: postgresql://user:pass@host:port/dbname
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
# Strip query params
DB_NAME="${DB_NAME%%\?*}"

echo "[backup] Dumping database: ${DB_NAME} @ ${DB_HOST}:${DB_PORT}"

PGPASSWORD="$DB_PASS" pg_dump \
  -h "$DB_HOST" \
  -p "$DB_PORT" \
  -U "$DB_USER" \
  -d "$DB_NAME" \
  --no-owner \
  --no-privileges \
  --format=plain \
  | gzip > "$BACKUP_FILE"

BACKUP_SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
echo "[backup] Backup complete: ${BACKUP_FILE} (${BACKUP_SIZE})"

# Upload to S3 if configured
if [ -n "$S3_BUCKET" ] && [ "$1" = "--s3" ]; then
  S3_KEY="${S3_BUCKET}/documind_backup_${TIMESTAMP}.sql.gz"
  echo "[backup] Uploading to ${S3_KEY}..."
  aws s3 cp "$BACKUP_FILE" "$S3_KEY" \
    --region "${AWS_REGION:-us-east-1}" \
    --storage-class STANDARD_IA
  echo "[backup] Upload complete"

  # Apply retention: delete backups older than RETENTION_DAYS
  echo "[backup] Applying ${RETENTION_DAYS}-day retention policy..."
  CUTOFF=$(date -u -d "${RETENTION_DAYS} days ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
    || date -u -v"-${RETENTION_DAYS}d" +%Y-%m-%dT%H:%M:%SZ)  # macOS fallback

  aws s3 ls "${S3_BUCKET}/" \
    | awk '{print $4}' \
    | while read -r KEY; do
        FILE_DATE=$(echo "$KEY" | grep -oP '\d{8}_\d{6}' | head -1)
        if [ -n "$FILE_DATE" ]; then
          FILE_TS=$(date -d "${FILE_DATE:0:8} ${FILE_DATE:9:2}:${FILE_DATE:11:2}:${FILE_DATE:13:2}" +%s 2>/dev/null || echo 0)
          CUTOFF_TS=$(date -d "$CUTOFF" +%s 2>/dev/null || echo 0)
          if [ "$FILE_TS" -lt "$CUTOFF_TS" ]; then
            echo "[backup] Deleting old backup: ${KEY}"
            aws s3 rm "${S3_BUCKET}/${KEY}"
          fi
        fi
      done
fi

echo "[backup] Done"
