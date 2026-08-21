# Incremental Ingestion Performance Design

## Context

The restored production-like dataset contains two 4.6 MB IBKR Flex artifacts, 29,944 raw records, 503 canonical trades, 12,207 canonical FX rows, and 372 daily snapshot rows. Historical diagnostics show that canonical processing remained stable at about 1.8 seconds and snapshot processing at 56–65 milliseconds, while the two complete runs took 10.97 and 19.33 seconds. Both reports were ready on the first poll; randomized first-poll waiting accounted for most of the difference.

An isolated benchmark of the current pipeline measured 4.2–4.5 seconds of local processing for a distinct artifact and 1.35 seconds for an exact duplicate. The distinct-artifact cost included 1.32 seconds for raw inserts, 0.83 seconds for 187 individual instrument upserts, 0.95 seconds for canonical event upserts, and 0.27 seconds to read the run's raw rows. The current design also reparses and attempts all raw-row conflicts for exact duplicates, reprocesses unchanged historical Flex rows when an artifact differs only in metadata, and recomputes snapshots from cumulative history.

## Goals

- Keep distinct IBKR source artifacts and their raw rows immutable and auditable.
- Make exact duplicate ingestion idempotent without repeated row, canonical, or snapshot work.
- Canonicalize only rows that are new or changed relative to their immediately preceding version during normal ingestion.
- Reduce database round trips and provide indexes for the new access patterns.
- Recompute snapshots only for instruments affected by changed events, marks, or FX rates.
- Make normal first-poll latency stable while retaining retry jitter.
- Persist actionable duration measurements for every ingestion stage.
- Preserve full deterministic reprocess behavior.

## Non-goals

- Deleting or compacting immutable raw artifacts or raw rows.
- Changing the externally configured IBKR Flex query or its date range.
- Automatically deleting canonical events when a later Flex report omits a previously reported row.
- Replacing FIFO accounting, valuation hierarchy, or FX fallback rules.
- Introducing a queue, distributed worker, or new runtime dependency.

## Selected Approach

Normal ingestion will remain append-only at the raw boundary and become change-driven after that boundary. An exact duplicate under the existing `(account_id, period_key, flex_query_id, payload_sha256)` identity will stop after artifact identity validation. A distinct artifact will retain all of its raw rows, then select only new or changed rows for canonical mapping. The resulting changed conids and currencies will define the snapshot scope.

Full reprocessing remains intentionally separate: it reads all raw rows for the requested period and reapplies canonical UPSERT rules without delta filtering.

This approach is preferred over indexing and batching alone because it removes repeated semantic work, and over narrowing the IBKR query because it retains broker corrections and does not require external configuration changes.

## Artifact and Raw-row Processing

The existing artifact identity remains authoritative. Ingestion still parses enough XML to obtain the report date and compute the payload hash before the artifact UPSERT.

When the artifact UPSERT reports `deduplicated=true`:

- do not build or insert raw-row parameter batches;
- report all extracted rows as deduplicated in diagnostics;
- do not run canonical mapping;
- do not rebuild snapshots;
- finalize the ingestion run successfully with explicit skip reasons.

When the artifact is distinct, every extracted raw row is persisted exactly as today. This preserves the full source version even when most rows are unchanged.

## Changed-row Selection

For normal ingestion, a row is changed when its `source_payload` differs from the most recent earlier row with the same:

- `account_id`;
- `flex_query_id`;
- `section_name`;
- `source_row_ref`.

Rows without a preceding version are new. Comparison is against the immediately preceding version, not any historical match, so a value that changes and later changes back is still processed correctly. Ordering uses raw-row creation time and primary key as deterministic tie-breakers.

The canonical repository will expose a dedicated changed-row read for ingestion. Existing all-row reads remain unchanged for reprocess workflows. A later artifact that omits a prior row does not delete its canonical event; destructive correction handling remains outside this optimization.

## Batched Canonical Persistence

Instrument requests will be deduplicated by conid in memory, then sent to one PostgreSQL batch UPSERT. The statement accepts a JSONB recordset, performs `INSERT ... SELECT ... ON CONFLICT`, and returns the canonical instrument rows required to resolve event foreign keys. Event UPSERT behavior and stable collision rules remain unchanged.

Normal ingestion maps and upserts only changed rows. This is especially important for `ConversionRates`, which currently account for 12,207 of 14,972 persisted rows per artifact. A metadata-only artifact change can therefore preserve a full raw version while performing zero canonical writes.

## Incremental Snapshot Scope

The affected snapshot scope is the union of:

- conids present in changed `Trades`, `CashTransactions`, `CorporateActions`, or `OpenPositions` rows;
- instruments whose currency matches `fromCurrency` in a changed `ConversionRates` row.

The snapshot service will accept an optional affected-conid and affected-currency scope. `None` retains the existing full rebuild behavior for explicit callers; empty scopes produce a successful no-op result.

For a scoped build:

- resolve affected conids and currencies to instrument IDs after canonical instrument UPSERTs;
- read trades, cashflows, corporate actions, and current-run open-position marks only for those instruments;
- read FX rows only for currencies required by the affected instruments and their events;
- recompute and UPSERT only affected daily snapshot rows;
- close and replace open position lots only for affected instruments;
- leave unrelated lots and snapshots unchanged.

If a changed row cannot be scoped safely, ingestion falls back to a full snapshot rebuild and records the reason. FX changes invalidate every instrument using the changed source currency, even when no instrument-specific raw event changed.

## Database Indexes

A new Alembic migration will add:

- `raw_record(ingestion_run_id, created_at_utc, raw_record_id)` for run-local canonical reads;
- `raw_record(account_id, flex_query_id, section_name, source_row_ref, created_at_utc DESC, raw_record_id DESC)` for prior-version lookup.

The migration will be additive and reversible. No existing data is rewritten or deleted.

## Polling Behavior

The first statement poll will wait exactly `ibkr_flex_initial_wait_seconds` (currently five seconds). Jitter applies only after a retryable first response. For attempt two and later, backoff starts at `ibkr_flex_backoff_base_seconds`, doubles per retry, remains capped by `ibkr_flex_backoff_max_seconds`, and uses the existing jitter bounds.

Transport timeout retries and upstream-directed minimum retry delays remain unchanged.

## Stage Timing Diagnostics

Durations use a monotonic clock and are stored as integer milliseconds. Diagnostics will expose:

- request transport duration;
- statement polling duration and cumulative poll-wait duration;
- preflight duration;
- XML extraction duration;
- artifact persistence duration;
- raw-row persistence duration;
- canonical raw-read and canonical mapping/persistence duration;
- snapshot duration;
- total run duration through the existing ingestion-run fields.

Completed stages will also retain existing counts, hashes, source identifiers, retry metadata, and skip reasons. The ingestion run detail API already exposes the diagnostic timeline, so no new API route is required.

## Error and Transaction Behavior

Each existing repository transaction boundary remains intact. A failed raw, canonical, or snapshot operation fails the ingestion run with the existing deterministic error mapping. Exact duplicates are successful runs, not errors. An incremental snapshot failure does not silently broaden or partially publish; only an explicitly detected unscopable change triggers the documented full-rebuild fallback.

## Compatibility and Rollout

- Existing raw and canonical rows require no data migration.
- The new indexes are applied before the optimized code runs.
- Existing repository read methods and full snapshot calls remain available.
- Reprocess continues to canonicalize its complete target period.
- The browser dashboard continues to show total run duration; detailed stage durations are available through ingestion-run diagnostics.
- No environment variables or third-party packages are added.

## Verification

Automated tests will prove:

- first-poll wait is fixed and later attempts retain bounded jitter;
- exact duplicate artifacts do not call raw-row insertion, canonical persistence, or snapshot persistence;
- distinct artifacts retain all raw rows while unchanged rows are excluded from normal canonical mapping;
- a changed row that reverts to an older value is still selected relative to its immediate predecessor;
- batch instrument UPSERT returns the same conid-to-instrument mapping as individual UPSERTs;
- correction rows update canonical mutable values without changing stable origin identity;
- changed conids and FX currencies produce the correct affected-instrument union;
- scoped snapshot builds do not modify unrelated lots or snapshots;
- unscopable changes fall back to a full snapshot with a diagnostic reason;
- migration upgrade/downgrade creates and removes both indexes;
- stage diagnostics contain all required duration fields;
- the seeded PostgreSQL ingestion-to-report scenario remains deterministic;
- Ruff, MyPy, dependency audit, the full test suite, Docker health, and an isolated performance benchmark pass.

The performance benchmark will report observed timings rather than enforce brittle wall-clock thresholds. Behavioral assertions—number of rows processed, repository calls skipped, and affected instruments written—are the regression gates.
