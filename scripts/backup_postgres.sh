#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"

backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T -e BACKUP_STAMP="$backup_stamp" postgres sh -eu -c '
stage="/backups/staging/$BACKUP_STAMP"
archive="/backups/base/$BACKUP_STAMP.tar.gz"
case "$stage" in /backups/staging/*) ;; *) exit 2 ;; esac
mkdir -p "$stage" /backups/base /backups/weekly /backups/monthly /backups/catalog
pg_basebackup -h /var/run/postgresql -U "$POSTGRES_USER" -D "$stage" --format=plain --checkpoint=fast --wal-method=stream --manifest-checksums=SHA256
pg_verifybackup "$stage"
tar -C "$stage" -czf "$archive" .
(cd /backups/base && sha256sum "$BACKUP_STAMP.tar.gz") > "$archive.sha256"
printf "%s\t%s\t%s\n" "$BACKUP_STAMP" "$archive" "verified" >> /backups/catalog/base-backups.tsv
if [ "$(date -u +%u)" = "7" ]; then
    cp "$archive" "$archive.sha256" /backups/weekly/
fi
if [ "$(date -u +%d)" = "01" ]; then
    cp "$archive" "$archive.sha256" /backups/monthly/
fi
rm -rf "$stage"
find /backups/base -type f -name "*.tar.gz" -mtime +14 -delete
find /backups/base -type f -name "*.tar.gz.sha256" -mtime +14 -delete
find /backups/weekly -type f -name "*.tar.gz" -mtime +56 -delete
find /backups/weekly -type f -name "*.tar.gz.sha256" -mtime +56 -delete
find /backups/monthly -type f -name "*.tar.gz" -mtime +366 -delete
find /backups/monthly -type f -name "*.tar.gz.sha256" -mtime +366 -delete
printf "%s\n" "$archive"
'
docker compose exec -T postgres sh -s -- /backups/base /var/lib/postgresql/wal_archive < scripts/prune_wal_archive.sh
