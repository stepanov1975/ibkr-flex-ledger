#!/bin/sh
set -eu

# Run inside PostgreSQL's container, after verified base-backup retention.
backup_dir=${1:-/backups/base}
wal_dir=${2:-/var/lib/postgresql/wal_archive}
export LC_ALL=C
set -- "$backup_dir"/*.tar.gz
oldest=$1
if [ ! -f "$oldest" ] || [ ! -f "$oldest.sha256" ]; then
    echo "WAL cleanup requires a checksummed retained base backup" >&2
    exit 1
fi
(cd "$backup_dir" && sha256sum -c "$(basename "$oldest").sha256") >/dev/null
label=$(tar -xOzf "$oldest" ./backup_label)
cutoff=$(printf '%s\n' "$label" | sed -n 's/^START WAL LOCATION: .* (file \([0-9A-F]\{24\}\))$/\1/p')
if [ "${#cutoff}" -ne 24 ]; then
    echo "WAL cleanup could not establish the oldest backup start segment" >&2
    exit 1
fi
case "$cutoff" in *[!0-9A-F]*) exit 1 ;; esac
pg_archivecleanup "$wal_dir" "$cutoff"
