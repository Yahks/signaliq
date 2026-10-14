#!/usr/bin/env bash
# SignalIQ Postgres backup script.
#
# Dumps the signaliq database to a timestamped .sql.gz file.
# Retains the last 7 daily backups by default.
#
# Usage:
#   ./scripts/backups/backup.sh                # uses defaults
#   BACKUP_DIR=/mnt/backups ./scripts/backups/backup.sh
#   RETENTION_DAYS=30 ./scripts/backups/backup.sh

set -euo pipefail

# ---- config ----
BACKUP_DIR="${BACKUP_DIR:-$HOME/signaliq_backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
CONTAINER="${CONTAINER:-signaliq-postgres-1}"
DB_USER="${DB_USER:-signaliq}"
DB_NAME="${DB_NAME:-signaliq}"

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
OUTFILE="$BACKUP_DIR/signaliq_${TIMESTAMP}.sql.gz"

echo "==> Backing up $DB_NAME from container $CONTAINER"
echo "    Output: $OUTFILE"

# pg_dump inside the container, gzip on the host
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" --no-owner --clean --if-exists \
  | gzip > "$OUTFILE"

SIZE=$(du -h "$OUTFILE" | cut -f1)
echo "    Done. Size: $SIZE"

# Prune old backups
echo "==> Pruning backups older than $RETENTION_DAYS days"
find "$BACKUP_DIR" -name "signaliq_*.sql.gz" -type f -mtime "+$RETENTION_DAYS" -print -delete

# Summary
echo ""
echo "==> Current backups:"
ls -lh "$BACKUP_DIR"/signaliq_*.sql.gz 2>/dev/null | tail -10 || echo "    (none)"

echo ""
echo "✅ Backup complete: $OUTFILE"
