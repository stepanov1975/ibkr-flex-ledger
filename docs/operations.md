# Operations, retention, backup, and recovery

The application exposes ingestion reliability measurements at `GET /operations/slo` and the browser dashboard at `/ui`. The API reports the frozen 30-day success-rate target, p95 duration target, breach thresholds, and consecutive-failure signal.

## Scheduled operations

The production Docker Compose host uses the checked-in systemd services and timers in
`deploy/systemd/`. Install and enable all five timers by following
`deploy/systemd/README.md`. The default UTC schedule is:

- verified backup daily at 02:00;
- 60-day diagnostics retention daily at 03:15;
- restore drill every Sunday at 04:00; and
- ingestion daily at 06:00; and
- outbound SLO alert evaluation every 15 minutes.

Timers are persistent and include a small randomized delay. Backup, retention, and restore
drill jobs share a non-blocking maintenance lock; ingestion and alert evaluation use separate
locks. A skipped overlap is reported as a failed systemd service and must be investigated in
the journal.

## Outbound SLO alerts

Configure one or both delivery channels in `/stock_app/.env`: `ALERT_WEBHOOK_URL` for an HTTP(S)
webhook, and `ALERT_SMTP_HOST`, `ALERT_SMTP_PORT`, `ALERT_SMTP_STARTTLS`,
`ALERT_SMTP_USERNAME`, `ALERT_SMTP_PASSWORD`, `ALERT_EMAIL_FROM`, and comma-separated
`ALERT_EMAIL_TO` for SMTP email. `ALERT_DELIVERY_TIMEOUT_SECONDS` controls the outbound request
timeout. The SMTP username and password are optional together; the SMTP host, sender, and at
least one recipient are required together.

Each configured channel receives a message only when the evaluated SLO state transitions between
healthy and alerting. The first healthy evaluation establishes a baseline without sending a
message. Failed channels retry the same transition on the next evaluation independently, while
channels that already delivered it remain deduplicated.

Run an evaluation manually with:

```bash
docker compose --project-name stock_app --env-file .env --file docker-compose.yml \
  exec -T app python -m app.main alerts-evaluate
```

Inspect scheduler failures with:

```bash
systemctl status ibkr-flex-ledger-alerts.service
journalctl -u ibkr-flex-ledger-alerts.service
```

Delivery state is persisted after an SMTP send succeeds. A rare process crash after the SMTP
server accepts an email but before that state is written can cause a duplicate email on the next
evaluation.

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
it does not contact IBKR. The ordinary scoped HTTP reprocess endpoint is deliberately
non-cleanup. Only the operator CLI path below may remove derived snapshot dates that are not
backed by a selected artifact in the exact account/period/query scope, and it may be used only
after the backup and cleanup-candidate report have been recorded.

### 0. Pin the reviewed checkout and Compose project

Run the entire repair from one shell. This wrapper always addresses the existing `stock_app`
Compose project, loads `/stock_app/.env`, and builds from the exact reviewed checkout.
`REPAIR_CHECKOUT` defaults to the merged main checkout; change it only when a different
reviewed checkout is intentionally being used. Replace `REVIEWED_COMMIT` and do not
continue unless every check succeeds:

```bash
REPAIR_CHECKOUT=${REPAIR_CHECKOUT:-/stock_app}
REVIEWED_COMMIT='replace-with-reviewed-commit-sha'

test -f "$REPAIR_CHECKOUT/docker-compose.yml"
test "$(git -C "$REPAIR_CHECKOUT" rev-parse HEAD)" = "$REVIEWED_COMMIT"
test -z "$(git -C "$REPAIR_CHECKOUT" status --porcelain --untracked-files=all)"

repair_compose() {
    docker compose \
        --project-name stock_app \
        --env-file /stock_app/.env \
        --file "$REPAIR_CHECKOUT/docker-compose.yml" \
        "$@"
}

repair_compose config --quiet
```

If a new shell is opened, redefine and revalidate the wrapper before issuing another command.
Do not run a bare `docker compose` command during this repair.

### 1. Create and verify the repair backup

Create a custom-format dump in the PostgreSQL backup volume, verify that `pg_restore` can
read its catalog, and record its checksum and exact path:

```bash
repair_stamp=$(date -u +%Y%m%dT%H%M%SZ)
repair_compose exec -T -e REPAIR_STAMP="$repair_stamp" postgres sh -eu -c '
dump="/backups/broker-position-repair-$REPAIR_STAMP.dump"
pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file="$dump"
pg_restore --list "$dump" >/dev/null
sha256sum "$dump" > "$dump.sha256"
printf "%s\n" "$dump"
'
```

Do not continue if `pg_dump`, `pg_restore --list`, or checksum creation fails.

### 2. Record immutable counts, selected artifacts, and cleanup candidates

Run this query before reprocess and again after each replay. The two counts must remain
identical; explicit reprocess must not insert, update, or delete raw history.

```bash
repair_compose exec -T postgres sh -eu -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
SELECT 'raw_artifact' AS relation, count(*) AS row_count FROM raw_artifact
UNION ALL
SELECT 'raw_record', count(*) FROM raw_record
ORDER BY relation;
SQL
```

Discover scopes from exactly the artifacts production can replay. A non-null completion
pointer is eligible only when that completion run succeeded; a legacy null pointer is
eligible only when the immutable owner run succeeded. The final ranking exactly matches
production's newest-artifact-per-report-date selection. Record this output and require
`open_positions_present = true` for every selected row:

```bash
repair_compose exec -T postgres sh -eu -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
WITH eligible_artifact AS (
    SELECT artifact.raw_artifact_id, artifact.ingestion_run_id,
           artifact.completed_ingestion_run_id, artifact.account_id,
           artifact.period_key, artifact.flex_query_id, artifact.report_date_local,
           artifact.created_at_utc,
           count(*) OVER (
               PARTITION BY artifact.account_id, artifact.period_key,
                            artifact.flex_query_id, artifact.report_date_local
           ) AS eligible_artifact_count,
           row_number() OVER (
               PARTITION BY artifact.account_id, artifact.period_key,
                            artifact.flex_query_id, artifact.report_date_local
               ORDER BY artifact.created_at_utc DESC, artifact.raw_artifact_id DESC
           ) AS report_date_rank
    FROM raw_artifact artifact
    JOIN ingestion_run owner
      ON owner.ingestion_run_id = artifact.ingestion_run_id
    LEFT JOIN ingestion_run completion
      ON completion.ingestion_run_id = artifact.completed_ingestion_run_id
    WHERE artifact.report_date_local IS NOT NULL
      AND (
          (artifact.completed_ingestion_run_id IS NOT NULL AND completion.status = 'success')
          OR
          (artifact.completed_ingestion_run_id IS NULL AND owner.status = 'success')
      )
)
SELECT eligible.account_id, eligible.period_key, eligible.flex_query_id,
       eligible.report_date_local, eligible.raw_artifact_id,
       eligible.ingestion_run_id, eligible.completed_ingestion_run_id,
       eligible.eligible_artifact_count,
       EXISTS (
           SELECT 1
           FROM raw_record position_row
           WHERE position_row.raw_artifact_id = eligible.raw_artifact_id
             AND position_row.section_name = 'OpenPositions'
       ) AS open_positions_present
FROM eligible_artifact eligible
WHERE eligible.report_date_rank = 1
ORDER BY eligible.account_id, eligible.period_key, eligible.flex_query_id,
         eligible.report_date_local;
SQL
```

Choose one exact tuple from that output, never values inferred from dates, then generate the
pre-delete report with the same selection and cleanup predicates as production. This report
uses every raw-artifact owner run in the explicit scope for cleanup discovery, while supported
dates come only from the eligible selected artifacts. Save the complete output. A status of
`ABORT_EMPTY_SELECTION` is a hard stop; an empty selection must never be treated as success.

```bash
REPAIR_ACCOUNT_ID='replace-with-recorded-account-id'
TARGET_PERIOD='replace-with-recorded-period-key'
TARGET_QUERY='replace-with-recorded-flex-query-id'

repair_compose exec -T -e REPAIR_ACCOUNT_ID="$REPAIR_ACCOUNT_ID" app sh -eu -c '
test "$ACCOUNT_ID" = "$REPAIR_ACCOUNT_ID" || {
    printf "account mismatch: configured=%s expected=%s\n" "$ACCOUNT_ID" "$REPAIR_ACCOUNT_ID" >&2
    exit 1
}
'

repair_compose exec -T \
  -e REPAIR_ACCOUNT_ID="$REPAIR_ACCOUNT_ID" \
  -e TARGET_PERIOD="$TARGET_PERIOD" \
  -e TARGET_QUERY="$TARGET_QUERY" \
  postgres sh -eu -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  -v account_id="$REPAIR_ACCOUNT_ID" -v period_key="$TARGET_PERIOD" -v flex_query_id="$TARGET_QUERY"' <<'SQL'
CREATE TEMP TABLE repair_selected_artifact AS
WITH eligible_artifact AS (
    SELECT artifact.raw_artifact_id, artifact.ingestion_run_id,
           artifact.report_date_local, artifact.created_at_utc,
           row_number() OVER (
               PARTITION BY artifact.report_date_local
               ORDER BY artifact.created_at_utc DESC, artifact.raw_artifact_id DESC
           ) AS report_date_rank
    FROM raw_artifact artifact
    JOIN ingestion_run owner
      ON owner.ingestion_run_id = artifact.ingestion_run_id
    LEFT JOIN ingestion_run completion
      ON completion.ingestion_run_id = artifact.completed_ingestion_run_id
    WHERE artifact.account_id = :'account_id'
      AND artifact.period_key = :'period_key'
      AND artifact.flex_query_id = :'flex_query_id'
      AND artifact.report_date_local IS NOT NULL
      AND (
          (artifact.completed_ingestion_run_id IS NOT NULL AND completion.status = 'success')
          OR
          (artifact.completed_ingestion_run_id IS NULL AND owner.status = 'success')
      )
)
SELECT eligible.raw_artifact_id, eligible.ingestion_run_id,
       eligible.report_date_local,
       EXISTS (
           SELECT 1
           FROM raw_record position_row
           WHERE position_row.raw_artifact_id = eligible.raw_artifact_id
             AND position_row.section_name = 'OpenPositions'
       ) AS open_positions_present
FROM eligible_artifact eligible
WHERE eligible.report_date_rank = 1;

CREATE TEMP TABLE repair_cleanup_candidate AS
WITH scoped_owner_run AS (
    SELECT DISTINCT artifact.ingestion_run_id
    FROM raw_artifact artifact
    WHERE artifact.account_id = :'account_id'
      AND artifact.period_key = :'period_key'
      AND artifact.flex_query_id = :'flex_query_id'
)
SELECT snapshot.report_date_local, count(*) AS row_count
FROM pnl_snapshot_daily snapshot
WHERE snapshot.account_id = :'account_id'
  AND snapshot.ingestion_run_id IN (SELECT ingestion_run_id FROM scoped_owner_run)
  AND NOT EXISTS (
      SELECT 1
      FROM repair_selected_artifact selected
      WHERE selected.report_date_local = snapshot.report_date_local
  )
GROUP BY snapshot.report_date_local;

SELECT :'account_id' AS account_id, :'period_key' AS period_key,
       :'flex_query_id' AS flex_query_id,
       count(*) AS selected_artifact_count,
       CASE WHEN count(*) = 0 THEN 'ABORT_EMPTY_SELECTION'
            WHEN NOT bool_and(selected.open_positions_present)
                THEN 'ABORT_MISSING_OPENPOSITIONS'
            ELSE 'READY_FOR_OPERATOR_CLI' END AS operator_status
FROM repair_selected_artifact selected;

SELECT selected.report_date_local, selected.raw_artifact_id,
       selected.ingestion_run_id, selected.open_positions_present
FROM repair_selected_artifact selected
ORDER BY selected.report_date_local;

SELECT count(*) AS latest_non_cash_broker_count
FROM repair_selected_artifact selected
JOIN raw_record position_row
  ON position_row.raw_artifact_id = selected.raw_artifact_id
 AND position_row.section_name = 'OpenPositions'
 AND position_row.source_row_ref LIKE 'OpenPositions:OpenPosition:%'
WHERE selected.report_date_local = (
    SELECT max(latest.report_date_local)
    FROM repair_selected_artifact latest
)
  AND UPPER(BTRIM(position_row.source_payload->>'assetCategory')) NOT IN ('CASH', 'FX');

SELECT report_date_local AS unsupported_date, row_count
FROM repair_cleanup_candidate
ORDER BY report_date_local;
SQL
```

Copy `latest_non_cash_broker_count` from the saved pre-delete output into the shell and
validate it before deployment:

```bash
EXPECTED_LATEST_BROKER_COUNT='replace-with-recorded-count'
case "$EXPECTED_LATEST_BROKER_COUNT" in
    ''|*[!0-9]*) printf 'expected broker count must be a non-negative integer\n' >&2; exit 1 ;;
esac
```

### 3. Deploy reviewed code and run the operator-only replay

Build and replace only the `app` service in the pinned project. The Compose file's relative
build context resolves from `REPAIR_CHECKOUT`, whose clean commit was verified in step 0:

```bash
repair_compose up -d --build app
repair_compose ps
curl --fail --silent http://127.0.0.1:8000/health
```

For each recorded scope, in chronological period order, rerun the account guard and then the
CLI. This is the only cleanup-capable entry point. Do not substitute the ordinary HTTP
reprocess route; that route intentionally replays without deletion. The CLI reads PostgreSQL
raw artifacts only and does not invoke the Flex adapter:

```bash
repair_compose exec -T -e REPAIR_ACCOUNT_ID="$REPAIR_ACCOUNT_ID" -e TARGET_PERIOD="$TARGET_PERIOD" -e TARGET_QUERY="$TARGET_QUERY" app sh -eu -c '
if ! test "$ACCOUNT_ID" = "$REPAIR_ACCOUNT_ID"; then
    printf "account mismatch: configured=%s expected=%s\n" "$ACCOUNT_ID" "$REPAIR_ACCOUNT_ID" >&2
    exit 1
fi
python -m app.main reprocess-run --period-key "$TARGET_PERIOD" --flex-query-id "$TARGET_QUERY"
'
```

Do not run another scope until the command exits zero. Rerun the immutable-count query and
require exact equality with step 2. Retain the new reprocess run identifier and its `raw_read`,
`snapshot`, and `snapshot_cleanup` diagnostics.

### 4. Verify the exact repaired scope

Run this verification with the same explicit account/period/query values. It repeats the
production eligibility rule, so legacy successful-owner artifacts remain included. It reports
the expected non-cash broker OpenPositions count for every selected report date, three final
snapshot discrepancies for every selected date (including zero rows), and all four production
reconciliation counters from the newest successful reprocess. Set
`EXPECTED_LATEST_BROKER_COUNT` to the latest count recorded before mutation. Require a
nonempty selected set, an exact latest-count match, three zero discrepancy rows per selected
date, and one four-counter diagnostic row per selected date.

```bash
repair_compose exec -T \
  -e REPAIR_ACCOUNT_ID="$REPAIR_ACCOUNT_ID" \
  -e TARGET_PERIOD="$TARGET_PERIOD" \
  -e TARGET_QUERY="$TARGET_QUERY" \
  -e EXPECTED_LATEST_BROKER_COUNT="$EXPECTED_LATEST_BROKER_COUNT" \
  postgres sh -eu -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  -v account_id="$REPAIR_ACCOUNT_ID" -v period_key="$TARGET_PERIOD" \
  -v flex_query_id="$TARGET_QUERY" -v expected_latest_broker_count="$EXPECTED_LATEST_BROKER_COUNT"' <<'SQL'
CREATE TEMP TABLE repair_selected_artifact AS
WITH eligible_artifact AS (
    SELECT artifact.raw_artifact_id, artifact.ingestion_run_id,
           artifact.account_id, artifact.report_date_local,
           row_number() OVER (
               PARTITION BY artifact.report_date_local
               ORDER BY artifact.created_at_utc DESC, artifact.raw_artifact_id DESC
           ) AS report_date_rank
    FROM raw_artifact artifact
    JOIN ingestion_run owner
      ON owner.ingestion_run_id = artifact.ingestion_run_id
    LEFT JOIN ingestion_run completion
      ON completion.ingestion_run_id = artifact.completed_ingestion_run_id
    WHERE artifact.account_id = :'account_id'
      AND artifact.period_key = :'period_key'
      AND artifact.flex_query_id = :'flex_query_id'
      AND artifact.report_date_local IS NOT NULL
      AND (
          (artifact.completed_ingestion_run_id IS NOT NULL AND completion.status = 'success')
          OR
          (artifact.completed_ingestion_run_id IS NULL AND owner.status = 'success')
      )
)
SELECT raw_artifact_id, ingestion_run_id, account_id, report_date_local
FROM eligible_artifact
WHERE report_date_rank = 1;

CREATE TEMP TABLE repair_broker AS
    SELECT selected.report_date_local,
           instrument.instrument_id,
           REPLACE(BTRIM(record.source_payload->>'position'), ',', '')::numeric AS position_qty
    FROM repair_selected_artifact selected
    JOIN raw_record record
      ON record.raw_artifact_id = selected.raw_artifact_id
     AND record.section_name = 'OpenPositions'
     AND record.source_row_ref LIKE 'OpenPositions:OpenPosition:%'
    JOIN instrument
      ON instrument.account_id = selected.account_id
     AND instrument.conid = record.source_payload->>'conid'
    WHERE UPPER(BTRIM(record.source_payload->>'assetCategory')) NOT IN ('CASH', 'FX');

CREATE TEMP TABLE repair_snapshot AS
    SELECT selected.report_date_local, daily.instrument_id, daily.position_qty
    FROM repair_selected_artifact selected
    JOIN pnl_snapshot_daily daily
      ON daily.account_id = selected.account_id
     AND daily.report_date_local = selected.report_date_local;

SELECT count(*) AS selected_artifact_count,
       CASE WHEN count(*) = 0 THEN 'ABORT_EMPTY_SELECTION'
            ELSE 'VERIFY_SELECTED_DATES' END AS verification_status
FROM repair_selected_artifact;

WITH broker_count AS (
    SELECT selected.report_date_local,
           count(broker.instrument_id) AS expected_broker_open_positions_count
    FROM repair_selected_artifact selected
    LEFT JOIN repair_broker broker USING (report_date_local)
    GROUP BY selected.report_date_local
)
SELECT report_date_local, expected_broker_open_positions_count,
       CASE WHEN report_date_local = max(report_date_local) OVER ()
            THEN CASE WHEN expected_broker_open_positions_count = :'expected_latest_broker_count'::bigint
                      THEN 'LATEST_RECORDED_COUNT_CONFIRMED'
                      ELSE 'ABORT_LATEST_COUNT_CHANGED' END
            ELSE 'NON_LATEST_SELECTED_DATE' END AS latest_count_status
FROM broker_count
ORDER BY report_date_local;

WITH discrepancy_type AS (
    SELECT discrepancy
    FROM (VALUES
        ('broker_missing_snapshot'),
        ('broker_quantity_mismatch'),
        ('nonzero_snapshot_absent_from_broker')
    ) AS value(discrepancy)
), discrepancy AS (
    SELECT broker.report_date_local, 'broker_missing_snapshot' AS discrepancy
    FROM repair_broker broker
    LEFT JOIN repair_snapshot snapshot USING (report_date_local, instrument_id)
    WHERE snapshot.instrument_id IS NULL
    UNION ALL
    SELECT broker.report_date_local, 'broker_quantity_mismatch'
    FROM repair_broker broker
    JOIN repair_snapshot snapshot USING (report_date_local, instrument_id)
    WHERE broker.position_qty IS DISTINCT FROM snapshot.position_qty
    UNION ALL
    SELECT snapshot.report_date_local, 'nonzero_snapshot_absent_from_broker'
    FROM repair_snapshot snapshot
    JOIN instrument USING (instrument_id)
    LEFT JOIN repair_broker broker USING (report_date_local, instrument_id)
    WHERE broker.instrument_id IS NULL
      AND snapshot.position_qty <> 0
      AND UPPER(instrument.asset_category) NOT IN ('CASH', 'FX')
)
SELECT selected.report_date_local,
       kind.discrepancy,
       count(found.discrepancy) AS row_count
FROM repair_selected_artifact selected
CROSS JOIN discrepancy_type kind
LEFT JOIN discrepancy found
  ON found.report_date_local = selected.report_date_local
 AND found.discrepancy = kind.discrepancy
GROUP BY selected.report_date_local, kind.discrepancy
ORDER BY selected.report_date_local, kind.discrepancy;

WITH ranked_reprocess AS (
    SELECT run.ingestion_run_id, run.diagnostics,
           row_number() OVER (
               ORDER BY run.started_at_utc DESC, run.ingestion_run_id DESC
           ) AS run_rank
    FROM ingestion_run run
    WHERE run.run_type = 'reprocess'
      AND run.status = 'success'
      AND run.account_id = :'account_id'
      AND run.period_key = :'period_key'
      AND run.flex_query_id = :'flex_query_id'
), snapshot_diagnostic AS (
    SELECT event->'details' AS details
    FROM ranked_reprocess run
    CROSS JOIN LATERAL jsonb_array_elements(run.diagnostics) AS event
    WHERE run.run_rank = 1
      AND event->>'stage' = 'snapshot'
      AND event->>'status' = 'completed'
)
SELECT :'account_id' AS account_id, :'period_key' AS period_key,
       :'flex_query_id' AS flex_query_id,
       selected.report_date_local,
       details->>'broker_position_match_count' AS broker_position_match_count,
       details->>'broker_position_mismatch_count' AS broker_position_mismatch_count,
       details->>'broker_only_position_count' AS broker_only_position_count,
       details->>'broker_absent_nonzero_fifo_count' AS broker_absent_nonzero_fifo_count,
       CASE WHEN details IS NULL THEN 'ABORT_MISSING_SNAPSHOT_DIAGNOSTIC'
            ELSE 'DIAGNOSTIC_PRESENT' END AS diagnostic_status
FROM repair_selected_artifact selected
LEFT JOIN snapshot_diagnostic diagnostic
  ON (diagnostic.details->>'report_date_local')::date = selected.report_date_local
ORDER BY selected.report_date_local;
SQL
```

Record provisional reasons and a scope-specific checksum with the same selected dates. The
provisional summary is restricted to the explicit account and selected dates; it cannot mix
another account or a globally latest date. The JSONB checksum preserves SQL `NULL` positions.

```bash
repair_compose exec -T \
  -e REPAIR_ACCOUNT_ID="$REPAIR_ACCOUNT_ID" \
  -e TARGET_PERIOD="$TARGET_PERIOD" \
  -e TARGET_QUERY="$TARGET_QUERY" \
  postgres sh -eu -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  -v account_id="$REPAIR_ACCOUNT_ID" -v period_key="$TARGET_PERIOD" -v flex_query_id="$TARGET_QUERY"' <<'SQL'
WITH eligible_artifact AS (
    SELECT artifact.report_date_local,
           row_number() OVER (
               PARTITION BY artifact.report_date_local
               ORDER BY artifact.created_at_utc DESC, artifact.raw_artifact_id DESC
           ) AS report_date_rank
    FROM raw_artifact artifact
    JOIN ingestion_run owner
      ON owner.ingestion_run_id = artifact.ingestion_run_id
    LEFT JOIN ingestion_run completion
      ON completion.ingestion_run_id = artifact.completed_ingestion_run_id
    WHERE artifact.account_id = :'account_id'
      AND artifact.period_key = :'period_key'
      AND artifact.flex_query_id = :'flex_query_id'
      AND artifact.report_date_local IS NOT NULL
      AND (
          (artifact.completed_ingestion_run_id IS NOT NULL AND completion.status = 'success')
          OR
          (artifact.completed_ingestion_run_id IS NULL AND owner.status = 'success')
      )
), selected_date AS (
    SELECT report_date_local
    FROM eligible_artifact
    WHERE report_date_rank = 1
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

WITH eligible_artifact AS (
    SELECT artifact.report_date_local,
           row_number() OVER (
               PARTITION BY artifact.report_date_local
               ORDER BY artifact.created_at_utc DESC, artifact.raw_artifact_id DESC
           ) AS report_date_rank
    FROM raw_artifact artifact
    JOIN ingestion_run owner
      ON owner.ingestion_run_id = artifact.ingestion_run_id
    LEFT JOIN ingestion_run completion
      ON completion.ingestion_run_id = artifact.completed_ingestion_run_id
    WHERE artifact.account_id = :'account_id'
      AND artifact.period_key = :'period_key'
      AND artifact.flex_query_id = :'flex_query_id'
      AND artifact.report_date_local IS NOT NULL
      AND (
          (artifact.completed_ingestion_run_id IS NOT NULL AND completion.status = 'success')
          OR
          (artifact.completed_ingestion_run_id IS NULL AND owner.status = 'success')
      )
), selected_date AS (
    SELECT report_date_local
    FROM eligible_artifact
    WHERE report_date_rank = 1
)
SELECT daily.report_date_local, daily.valuation_source, daily.fx_source,
       (daily.cost_basis IS NULL) AS cost_basis_missing,
       count(*) AS row_count
FROM pnl_snapshot_daily daily
JOIN selected_date USING (report_date_local)
WHERE daily.account_id = :'account_id'
  AND daily.provisional
GROUP BY daily.report_date_local, daily.valuation_source, daily.fx_source,
         (daily.cost_basis IS NULL)
ORDER BY daily.report_date_local, daily.valuation_source, daily.fx_source,
         cost_basis_missing;
SQL
```

Rerun the same guarded CLI command once, repeat both queries, and require an identical checksum
and identical discrepancy results. Explain every remaining provisional group; it is not
automatically a repair failure. Never continue from an empty selected set or when the latest
broker count differs from `EXPECTED_LATEST_BROKER_COUNT`.

### 5. Roll back if verification fails

1. Stop the application with `repair_compose stop app`; do not run ingestion or another
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
