#!/usr/bin/env bash
# SignalIQ Postgres restore script.
#
# Restores a .sql.gz backup into a target database.
# Creates required roles first so GRANT statements succeed.
#
# Usage:
#   ./scripts/backups/restore.sh <backup-file.sql.gz>
#
# ⚠️  This DROPS and recreates all objects in the target DB.
#     Run only against a test DB or a stopped production DB.

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 <backup-file.sql.gz>"
  exit 1
fi

BACKUP_FILE="$1"
CONTAINER="${CONTAINER:-signaliq-postgres-1}"
DB_USER="${DB_USER:-signaliq}"
DB_NAME="${DB_NAME:-signaliq}"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "❌ Backup file not found: $BACKUP_FILE"
  exit 1
fi

echo "⚠️  About to restore $BACKUP_FILE into $DB_NAME."
echo "    All existing objects in $DB_NAME will be dropped."
read -p "Continue? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 0
fi

echo "==> Step 1: ensure required roles exist"
docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" <<'SQL'
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signaliq') THEN
        CREATE ROLE signaliq NOINHERIT NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'signaliq_app') THEN
        CREATE ROLE signaliq_app LOGIN PASSWORD 'signaliq_app'
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
    END IF;
END
$$;
SQL

echo "==> Step 2: restore data"
gunzip -c "$BACKUP_FILE" | docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" \
  --quiet --set=ON_ERROR_STOP=off

echo ""
echo "✅ Restore complete."
echo "   Verify with:"
echo "     docker exec -it $CONTAINER psql -U $DB_USER -d $DB_NAME -c '\\dt'"
echo "     docker exec -it $CONTAINER psql -U $DB_USER -d $DB_NAME -c 'SELECT count(*) FROM news_events;'"
