#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"

drill_stamp=$(date -u +%Y%m%dT%H%M%SZ)
postgres_container=$(docker compose ps -q postgres)
backup_volume=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/backups"}}{{.Name}}{{end}}{{end}}' "$postgres_container")
archive_path=$(docker compose exec -T postgres sh -eu -c 'ls -1t /backups/base/*.tar.gz | head -1')
archive_name=$(basename "$archive_path")
restore_volume="stock_app_restore_drill_$drill_stamp"
restore_container="stock_app-restore-drill-$drill_stamp"
started_epoch=$(date +%s)

cleanup() {
    docker rm -f "$restore_container" >/dev/null 2>&1 || true
    docker volume rm "$restore_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker volume create "$restore_volume" >/dev/null
docker run --rm -e ARCHIVE_NAME="$archive_name" -v "$backup_volume:/backups:ro" -v "$restore_volume:/restore" postgres:17 sh -eu -c '
tar -xzf "/backups/base/$ARCHIVE_NAME" -C /restore
chown -R postgres:postgres /restore
'
docker run -d --name "$restore_container" -v "$restore_volume:/var/lib/postgresql/data" postgres:17 -c archive_mode=off >/dev/null

attempt=0
until docker exec "$restore_container" pg_isready -q; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        docker logs "$restore_container"
        exit 1
    fi
    sleep 1
done

database_user=$(docker compose exec -T postgres printenv POSTGRES_USER)
database_name=$(docker compose exec -T postgres printenv POSTGRES_DB)
docker exec "$restore_container" psql -v ON_ERROR_STOP=1 -U "$database_user" -d "$database_name" -c 'SELECT version_num FROM alembic_version;' >/dev/null
docker exec "$restore_container" psql -v ON_ERROR_STOP=1 -U "$database_user" -d "$database_name" -c 'SELECT count(*) FROM ingestion_run;' >/dev/null
docker exec "$restore_container" psql -v ON_ERROR_STOP=1 -U "$database_user" -d "$database_name" -c 'SELECT count(*) FROM pnl_snapshot_daily;' >/dev/null

ended_epoch=$(date +%s)
elapsed_seconds=$((ended_epoch - started_epoch))
mkdir -p var/restore-drills
evidence_path="var/restore-drills/$drill_stamp.json"
printf '{"drill_at_utc":"%s","backup":"%s","status":"success","elapsed_seconds":%s,"rto_target_seconds":14400}\n' "$drill_stamp" "$archive_name" "$elapsed_seconds" > "$evidence_path"
printf '%s\n' "$evidence_path"
