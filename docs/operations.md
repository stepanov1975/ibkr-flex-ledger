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

## Broker position repair by immutable replay

Explicit reprocess rebuilds canonical events and daily snapshots from stored raw artifacts;
it does not contact IBKR. Unlike normal replay, an explicit target may delete derived
snapshot dates that are not backed by a selected artifact in that exact
account/period/query scope. Complete the verified backup step before running any explicit
repair.

### 1. Create and verify the repair backup

Create a custom-format dump in the PostgreSQL backup volume, verify that `pg_restore` can
read its catalog, and record its checksum and exact path:

```bash
repair_stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T -e REPAIR_STAMP="$repair_stamp" postgres sh -eu -c '
dump="/backups/broker-position-repair-$REPAIR_STAMP.dump"
pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file="$dump"
pg_restore --list "$dump" >/dev/null
sha256sum "$dump" > "$dump.sha256"
printf "%s\n" "$dump"
'
```

Do not continue if `pg_dump`, `pg_restore --list`, or checksum creation fails.

### 2. Record immutable counts before repair

Run this query before reprocess and again after each replay. The two counts must remain
identical; explicit reprocess must not insert, update, or delete raw history.

```bash
docker compose exec -T postgres sh -eu -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
SELECT 'raw_artifact' AS relation, count(*) AS row_count FROM raw_artifact
UNION ALL
SELECT 'raw_record', count(*) FROM raw_record
ORDER BY relation;
SQL
```

Also record the target scopes and actual artifact dates before choosing commands. Select an
exact `account_id`, `period_key`, and `flex_query_id` tuple from this output; do not infer the
account from the dates:

```sql
SELECT account_id, period_key, flex_query_id, report_date_local, count(*) AS artifacts
FROM raw_artifact
GROUP BY account_id, period_key, flex_query_id, report_date_local
ORDER BY account_id, period_key, flex_query_id, report_date_local;
```

### 3. Reprocess each scope explicitly

Run known scopes in chronological period order. Set `REPAIR_ACCOUNT_ID` and `TARGET_QUERY` to
the exact values recorded in step 2. The CLI deliberately retains its existing period/query
contract, so each command first verifies that the app container's ambient
`ACCOUNT_ID` equals the explicit `REPAIR_ACCOUNT_ID` and aborts before cleanup on any mismatch.
These commands use only PostgreSQL raw artifacts and do not invoke the Flex adapter:

```bash
REPAIR_ACCOUNT_ID='replace-with-recorded-account-id'
TARGET_QUERY='replace-with-recorded-flex-query-id'

docker compose exec -T -e REPAIR_ACCOUNT_ID="$REPAIR_ACCOUNT_ID" -e TARGET_QUERY="$TARGET_QUERY" app sh -eu -c '
if ! test "$ACCOUNT_ID" = "$REPAIR_ACCOUNT_ID"; then
    printf "account mismatch: configured=%s expected=%s\n" "$ACCOUNT_ID" "$REPAIR_ACCOUNT_ID" >&2
    exit 1
fi
python -m app.main reprocess-run --period-key 2026-02-20 --flex-query-id "$TARGET_QUERY"
'

docker compose exec -T -e REPAIR_ACCOUNT_ID="$REPAIR_ACCOUNT_ID" -e TARGET_QUERY="$TARGET_QUERY" app sh -eu -c '
if ! test "$ACCOUNT_ID" = "$REPAIR_ACCOUNT_ID"; then
    printf "account mismatch: configured=%s expected=%s\n" "$ACCOUNT_ID" "$REPAIR_ACCOUNT_ID" >&2
    exit 1
fi
python -m app.main reprocess-run --period-key 2026-08-21 --flex-query-id "$TARGET_QUERY"
'
```

For another scope, replace the expected account, `--period-key`, and query with all three
values from one row of the scope query; never mix values from different rows, use an inferred
date, or omit one side of the CLI's explicit period/query pair. Require the account guard to
pass before every cleanup-capable command. Rerun the raw-count query after the commands and
require exact equality with the recorded before-state.

Inspect the new reprocess diagnostics. Snapshot events expose
`broker_position_match_count`, `broker_position_mismatch_count`,
`broker_only_position_count`, and `broker_absent_nonzero_fifo_count`; raw-read events list
the selected actual report dates, and cleanup events list candidates and deleted rows. Run
this query with the same three `psql -v` account/period/query bindings shown in step 4.

```sql
SELECT run.ingestion_run_id, event->>'stage' AS stage, event->'details' AS details
FROM ingestion_run run
CROSS JOIN LATERAL jsonb_array_elements(run.diagnostics) AS event
WHERE run.run_type = 'reprocess'
  AND run.account_id = :'account_id'
  AND run.period_key = :'period_key'
  AND run.flex_query_id = :'flex_query_id'
  AND event->>'stage' IN ('raw_read', 'snapshot', 'snapshot_cleanup')
ORDER BY run.started_at_utc DESC, run.ingestion_run_id, event->>'at_utc';
```

### 4. Verify broker authority and replay idempotence

For each repaired scope, set all three explicit values and run the verification query. It
selects the newest successful artifact for every actual report date within that exact scope,
not one globally newest artifact. Each selected date must return all three discrepancy rows,
and every count must be zero.

```bash
REPAIR_ACCOUNT_ID='replace-with-recorded-account-id'
TARGET_PERIOD='replace-with-recorded-period-key'
TARGET_QUERY='replace-with-recorded-flex-query-id'

docker compose exec -T \
  -e REPAIR_ACCOUNT_ID="$REPAIR_ACCOUNT_ID" \
  -e TARGET_PERIOD="$TARGET_PERIOD" \
  -e TARGET_QUERY="$TARGET_QUERY" \
  postgres sh -eu -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
    -v account_id="$REPAIR_ACCOUNT_ID" -v period_key="$TARGET_PERIOD" -v flex_query_id="$TARGET_QUERY"' <<'SQL'
WITH ranked_artifact AS (
    SELECT artifact.raw_artifact_id,
           artifact.account_id,
           artifact.report_date_local,
           row_number() OVER (
               PARTITION BY artifact.report_date_local
               ORDER BY artifact.created_at_utc DESC, artifact.raw_artifact_id DESC
           ) AS artifact_rank
    FROM raw_artifact artifact
    JOIN ingestion_run completed
      ON completed.ingestion_run_id = artifact.completed_ingestion_run_id
     AND completed.status = 'success'
    WHERE artifact.account_id = :'account_id'
      AND artifact.period_key = :'period_key'
      AND artifact.flex_query_id = :'flex_query_id'
      AND artifact.report_date_local IS NOT NULL
), selected_artifact AS (
    SELECT raw_artifact_id, account_id, report_date_local
    FROM ranked_artifact
    WHERE artifact_rank = 1
), broker AS (
    SELECT selected.report_date_local,
           instrument.instrument_id,
           (record.source_payload->>'position')::numeric AS position_qty
    FROM selected_artifact selected
    JOIN raw_record record
      ON record.raw_artifact_id = selected.raw_artifact_id
     AND record.section_name = 'OpenPositions'
     AND record.source_row_ref LIKE 'OpenPositions:OpenPosition:%'
    JOIN instrument
      ON instrument.account_id = selected.account_id
     AND instrument.conid = record.source_payload->>'conid'
    WHERE UPPER(record.source_payload->>'assetCategory') NOT IN ('CASH', 'FX')
), snapshot AS (
    SELECT selected.report_date_local, daily.instrument_id, daily.position_qty
    FROM selected_artifact selected
    JOIN pnl_snapshot_daily daily
      ON daily.account_id = selected.account_id
     AND daily.report_date_local = selected.report_date_local
), discrepancy_type AS (
    SELECT discrepancy
    FROM (VALUES
        ('broker_missing_snapshot'),
        ('broker_quantity_mismatch'),
        ('nonzero_snapshot_absent_from_broker')
    ) AS value(discrepancy)
), discrepancy AS (
    SELECT broker.report_date_local, 'broker_missing_snapshot' AS discrepancy
    FROM broker
    LEFT JOIN snapshot USING (report_date_local, instrument_id)
    WHERE snapshot.instrument_id IS NULL
    UNION ALL
    SELECT broker.report_date_local, 'broker_quantity_mismatch'
    FROM broker
    JOIN snapshot USING (report_date_local, instrument_id)
    WHERE broker.position_qty IS DISTINCT FROM snapshot.position_qty
    UNION ALL
    SELECT snapshot.report_date_local, 'nonzero_snapshot_absent_from_broker'
    FROM snapshot
    JOIN instrument USING (instrument_id)
    LEFT JOIN broker USING (report_date_local, instrument_id)
    WHERE broker.instrument_id IS NULL
      AND snapshot.position_qty <> 0
      AND UPPER(instrument.asset_category) NOT IN ('CASH', 'FX')
)
SELECT selected.report_date_local,
       kind.discrepancy,
       count(found.discrepancy) AS row_count
FROM selected_artifact selected
CROSS JOIN discrepancy_type kind
LEFT JOIN discrepancy found
  ON found.report_date_local = selected.report_date_local
 AND found.discrepancy = kind.discrepancy
GROUP BY selected.report_date_local, kind.discrepancy
ORDER BY selected.report_date_local, kind.discrepancy;
SQL
```

Compare the returned dates with the scope-discovery output to prove every selected actual
report date was checked. Record a scope-specific snapshot checksum, rerun the same guarded
explicit command once, and require the second checksum to match the first. The JSONB array
preserves the position of SQL `NULL` values; unlike `concat_ws`, it cannot collapse distinct
nullable-column layouts into the same serialization.

Run the checksum query through the same parameterized `psql` wrapper and with the same three
scope values used for the mismatch query.

```sql
WITH ranked_artifact AS (
    SELECT artifact.report_date_local,
           row_number() OVER (
               PARTITION BY artifact.report_date_local
               ORDER BY artifact.created_at_utc DESC, artifact.raw_artifact_id DESC
           ) AS artifact_rank
    FROM raw_artifact artifact
    JOIN ingestion_run completed
      ON completed.ingestion_run_id = artifact.completed_ingestion_run_id
     AND completed.status = 'success'
    WHERE artifact.account_id = :'account_id'
      AND artifact.period_key = :'period_key'
      AND artifact.flex_query_id = :'flex_query_id'
      AND artifact.report_date_local IS NOT NULL
), selected_date AS (
    SELECT report_date_local
    FROM ranked_artifact
    WHERE artifact_rank = 1
)
SELECT md5(COALESCE(string_agg(
    jsonb_build_array(
        daily.account_id, daily.report_date_local, daily.instrument_id,
        daily.position_qty, daily.cost_basis, daily.realized_pnl,
        daily.unrealized_pnl, daily.total_pnl, daily.fees,
        daily.withholding_tax, daily.currency, daily.provisional,
        daily.valuation_source, daily.fx_source, daily.ingestion_run_id
    )::text,
    E'\n' ORDER BY daily.report_date_local, daily.instrument_id
), '')) AS snapshot_checksum
FROM pnl_snapshot_daily daily
JOIN selected_date USING (report_date_local)
WHERE daily.account_id = :'account_id';
```

Review remaining provisional rows by reason rather than treating every provisional row as
a failed repair:

```sql
SELECT valuation_source, fx_source, (cost_basis IS NULL) AS cost_basis_missing,
       count(*) AS row_count
FROM pnl_snapshot_daily
WHERE provisional
GROUP BY valuation_source, fx_source, (cost_basis IS NULL)
ORDER BY valuation_source, fx_source, cost_basis_missing;
```

### 5. Roll back if verification fails

1. Stop the application with `docker compose stop app`; do not run ingestion or another
   repair against the failed state.
2. Recheck the saved dump with `pg_restore --list` and `sha256sum --check` using the exact
   dump and checksum paths recorded in step 1.
3. Restore the dump into a new, empty replacement database with `pg_restore --exit-on-error`
   rather than overwriting the failed database. Keep the failed database for diagnosis.
4. Point `DATABASE_URL` at the replacement database, run `alembic current`, repeat the raw
   counts and broker mismatch query, then start the app and verify `/health`.
5. Switch normal traffic only after the restored state passes validation. Record the dump
   path, checksum, replacement database name, validation output, and operator sign-off.

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
