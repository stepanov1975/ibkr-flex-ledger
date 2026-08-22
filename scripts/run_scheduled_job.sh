#!/bin/sh

set -eu

usage() {
    echo "usage: $0 {ingestion|retention|alerts|backup|restore-drill}" >&2
}

if [ "$#" -ne 1 ]; then
    usage
    exit 64
fi

job_name=$1
stock_app_root=${STOCK_APP_ROOT:-/stock_app}
lock_root=${STOCK_APP_LOCK_ROOT:-/run/lock}

case "$job_name" in
    ingestion)
        lock_name=ingestion.lock
        set -- docker compose --project-name stock_app --env-file .env \
            --file docker-compose.yml exec -T app python -m app.main ingestion-run
        ;;
    retention)
        lock_name=maintenance.lock
        set -- docker compose --project-name stock_app --env-file .env \
            --file docker-compose.yml exec -T app python -m app.main diagnostics-retention
        ;;
    alerts)
        lock_name=alerts.lock
        set -- docker compose --project-name stock_app --env-file .env \
            --file docker-compose.yml exec -T app python -m app.main alerts-evaluate
        ;;
    backup)
        lock_name=maintenance.lock
        set -- "$stock_app_root/scripts/backup_postgres.sh"
        ;;
    restore-drill)
        lock_name=maintenance.lock
        set -- "$stock_app_root/scripts/restore_drill.sh"
        ;;
    *)
        usage
        exit 64
        ;;
esac

if [ ! -f "$stock_app_root/.env" ] || [ ! -f "$stock_app_root/docker-compose.yml" ]; then
    echo "stock app deployment is incomplete at $stock_app_root" >&2
    exit 66
fi

mkdir -p "$lock_root"
cd "$stock_app_root"

exec flock --nonblock "$lock_root/$lock_name" "$@"
