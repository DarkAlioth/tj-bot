#!/usr/bin/env bash
# Daily PostgreSQL backup for tj-bot. Intended for cron:
#   0 4 * * * /home/sony/projects/TJ_BOT/scripts/backup_db.sh
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/tj-bot}"
KEEP="${KEEP:-14}"
CONTAINER="${CONTAINER:-db}"

ENV_FILE="$(dirname "$0")/../.env"
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | cut -d= -f2-)"
POSTGRES_DB="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | cut -d= -f2-)"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y-%m-%d_%H%M)"
docker exec "$CONTAINER" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" \
  | gzip > "$BACKUP_DIR/tj-bot_${STAMP}.sql.gz"

ls -1t "$BACKUP_DIR"/tj-bot_*.sql.gz | tail -n +$((KEEP + 1)) | xargs -r rm --
