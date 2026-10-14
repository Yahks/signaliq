# SignalIQ Backups

## Take a backup

    ./scripts/backups/backup.sh

Backups go to `~/signaliq_backups/` by default. Override with:

    BACKUP_DIR=/mnt/backups ./scripts/backups/backup.sh

Retention: 7 days by default. Override with `RETENTION_DAYS=30`.

## Schedule daily backups

Add to crontab (`crontab -e`):

    0 3 * * * cd ~/Desktop/signaliq && ./scripts/backups/backup.sh >> ~/signaliq_backups/backup.log 2>&1

## Restore a backup

    ./scripts/backups/restore.sh ~/signaliq_backups/signaliq_20260918_120000.sql.gz

Warning: restore drops all existing objects. Run only against a test DB, or a stopped production DB.

## Tested restore workflow

1. Take a backup: `./scripts/backups/backup.sh`
2. Spin up a clean Postgres:
       docker run -d --name pg-test -p 5434:5432 \
         -e POSTGRES_USER=signaliq -e POSTGRES_PASSWORD=signaliq \
         -e POSTGRES_DB=signaliq pgvector/pgvector:pg16
3. Restore into it:
       CONTAINER=pg-test ./scripts/backups/restore.sh <backup>
4. Verify:
       docker exec -it pg-test psql -U signaliq -d signaliq -c "SELECT count(*) FROM news_events;"
5. Clean up: `docker rm -f pg-test`

Run this monthly to prove backups are usable.
