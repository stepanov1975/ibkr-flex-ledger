# Operations, retention, backup, and recovery

The application exposes ingestion reliability measurements at `GET /operations/slo` and the browser dashboard at `/ui`. The API reports the frozen 30-day success-rate target, p95 duration target, breach thresholds, and consecutive-failure signal.

## Diagnostic retention

Run daily:

```bash
docker compose exec -T app python -m app.main diagnostics-retention
```

The job archives ingestion diagnostics older than 60 days as checksum-verified compressed JSONL before clearing only the derived `ingestion_run.diagnostics` payload. Immutable `raw_artifact` and `raw_record` data is never deleted.

## Backup schedule

PostgreSQL is configured with WAL archiving and a five-minute archive timeout. Schedule a verified physical base backup every 24 hours:

```bash
./scripts/backup_postgres.sh
```

The backup command runs `pg_basebackup`, verifies its manifest with `pg_verifybackup`, records a SHA-256 checksum and catalog entry, and retains 14 daily archives. Sunday backups are also retained for 8 weeks and first-of-month backups for 12 months. Copy the `postgres_backups` and `postgres_wal_archive` volumes to separate durable storage; retaining them only on the application host does not protect against host loss.

## Restore drill

Run weekly against the latest verified backup:

```bash
./scripts/restore_drill.sh
```

The drill restores into a temporary isolated Docker volume, starts PostgreSQL, and verifies migration, ingestion-run, and snapshot queries. Evidence is written under ignored `var/restore-drills/`. The temporary container and volume are removed automatically.

For the monthly full drill, also start the application against the restored database and verify `/health`, `/ingestion/runs`, `/reports/pnl/by-instrument`, and `/reports/reconciliation/diff`. Record elapsed time; the frozen RTO is four hours.

## Incident recovery

1. Open an incident when two ingestion runs fail consecutively, success rate drops below 98%, a run exceeds 30 minutes, backup verification fails, or recovery remains unresolved for two hours.
2. Assign the app owner and select the latest verified base backup preceding the desired recovery timestamp.
3. Restore the base backup into an isolated PostgreSQL data directory.
4. Copy archived WAL files into the recovery source and configure `recovery_target_time` for the selected timestamp; the frozen RPO is 15 minutes.
5. Start PostgreSQL, allow WAL replay to finish, and promote it.
6. Run `alembic current`, verify the health and report endpoints listed above, and compare the latest ingestion identifiers with the backup catalog.
7. Redirect application traffic only after validation, document actual RPO/RTO, remediation, owner, and sign-off.
