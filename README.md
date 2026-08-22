# ibkr-flex-ledger

IBKR Flex Ledger

Self-hosted portfolio accounting and analytics app for Interactive Brokers (IBKR) Flex data, focused on auditable, reconciliable stock portfolio metrics.

## Implementation policy (important)

The codebase must be structured in a strongly modular way from the start because additional functionality will be added later.

### Reference code boundary (mandatory)

Code under `references/` is reference material only. It is not part of this application runtime.

- Do not import Python/TypeScript modules from `references/` into project code.
- Do not call CLI entry points from `references/` as part of app jobs or services.
- Reuse ideas and patterns only; implement project-native code in the main application modules.

- `max_plan.md` is the reference for end-state architecture and future modules.
- `references/REFERENCE_NOTES.md` is the reference index of external projects and reusable patterns.
- Features described in `max_plan.md` are not initial implementation scope.
- MVP implementation should keep stable boundaries so future capabilities can be added with minimal changes to already working modules.
- New domains (options, strategies, advanced performance, expanded corporate actions) should be added as new modules that integrate through existing interfaces.
- All database operations must be centralized in the database layer only. No direct database queries are allowed outside `db` modules.
- Before implementing any feature or significant bug fix, scan `references/REFERENCE_NOTES.md` first to reuse proven patterns and avoid reinventing existing solutions.

## What this project does

- Imports IBKR Flex reports on a schedule and stores immutable raw payloads.
- Normalizes broker records into canonical events (trades, cashflows, FX, and flagged corporate actions).
- Computes positions and P&L with a stocks-first FIFO ledger, including contract-multiplier economics for supported options.
- Supports labels and notes for grouped analysis and reporting drilldowns.
- Provides reconciliation views to compare broker-aligned and economic calculations with traceability to source rows.

## MVP scope

Included in MVP:

1. Automated ingestion + ingestion audit trail
2. Canonical event mapping pipeline
3. Stocks-first positions and P&L engine
4. Labels and notes workflows
5. Reporting with drilldowns
6. Reconciliation mode and diffs
7. Operational reliability (schema-drift checks, reprocess support, diagnostics)

Out of scope for MVP:

- Full options lifecycle and deliverable accounting beyond supported option trades and broker position valuation
- Real-time market data and risk dashboards
- Trade execution automation

## Architecture summary

- Runtime: Ubuntu LXC deployment
- Services: app + PostgreSQL (Docker Compose)
- Scheduler: systemd timers for ingestion and operational maintenance CLIs
- Layering:
	- Adapter layer: Flex fetch and immutable raw persistence
	- Mapping layer: raw records to canonical events
	- Ledger layer: lots, positions, P&L
	- Analytics layer: label/instrument aggregations
	- API/UI layer: CRUD and reporting
	- Job layer: ingestion, reprocess, and snapshot workflows

Core rule: raw inputs are immutable; derived datasets are reproducible from raw records.

Modularity rule: architecture must be prepared for future domains without forcing rewrites of already working MVP parts.

Data-access rule: API routes, services, adapters, CLI, and jobs must use database-layer interfaces/repositories rather than issuing direct SQL/ORM queries.

## Environment status

- PostgreSQL server is already installed in this environment.
- Active cluster: `17/main`
- Status: online and accepting connections on port `5432`

## Data model highlights

Core entities:

- instrument, label, instrument_label, note
- ingestion_run, raw_record
- event_trade_fill, event_cashflow, event_fx, event_corp_action
- position_lot, pnl_snapshot_daily

Traceability is first-class: report values are designed to link back to canonical events and original raw records.

## MVP milestones

1. Foundation and project skeleton
2. Ingestion and raw persistence
3. Canonical event mapping
4. Positions and P&L engine
5. Labels, notes, and reporting
6. Reconciliation and audit UX

For full milestone-level acceptance criteria and implementation details, see:

- `MVP.md`
- `MVP_spec_freeze.md` (frozen MVP implementation values and contracts)
- `implementation_task_list.md` (outcome-ordered implementation execution checklist)
- `initial_plan.md`
- `max_plan.md` (reference architecture; not initial scope)
- `references/REFERENCE_NOTES.md` (external reference projects and reuse guidance)

## Quickstart

1. Create a virtual environment in the project root:

	```bash
	python3 -m venv .venv
	```

2. Activate the environment:

	```bash
	source .venv/bin/activate
	```

3. Install dependencies:

	```bash
	pip install -r requirements.txt
	pip install -r requirements-dev.txt
	```

## Runtime foundation (Task 1)

The Task 1 runtime foundation now includes:

- FastAPI application skeleton with modular layer boundaries under `app/`
- Centralized database connectivity in `app/db/` only
- Startup settings via `.env` plus environment variable overrides
- Health endpoint at `GET /health` with database connectivity verification

### One-command local stack (Docker Compose)

```bash
docker compose up -d
```

The app container applies Alembic migrations before starting the API. PostgreSQL
data is stored in the named `postgres_data` volume.

Service endpoints:

- App: `http://127.0.0.1:8000`
- Health: `http://127.0.0.1:8000/health`
- PostgreSQL host port: `5433` (container port `5432`)
- Portfolio dashboard: `http://127.0.0.1:8000/ui`
- OpenAPI documentation: `http://127.0.0.1:8000/docs`

The MVP now includes corporate-action manual cases, instrument labels and notes,
PnL/provenance/reconciliation reports, stable CSV v1 exports, and operational
SLO visibility. Backup, retention, and restore procedures are documented in
`docs/operations.md`.

### Database runtime mode (Docker-only recommended)

Project runtime is standardized on Docker PostgreSQL.

- Recommended: do not run a host PostgreSQL server for this project.
- Keep database service in Docker Compose only to avoid hostname/port drift.

`DATABASE_URL` must match where the app process runs:

- App running inside Docker network: `postgresql+psycopg://stock_user:stock_password@postgres:5432/stock_app`
- App running from host shell: `postgresql+psycopg://stock_user:stock_password@127.0.0.1:5433/stock_app`

### Configuration loading

Runtime settings are defined in `app/config/settings.py` and loaded in this order:

1. Environment variables
2. `.env` file values

Credential storage guidance:

- Keep real credentials in local `.env` only (gitignored).
- Use `.env.example` as the committed template for required variables.
- `docker-compose.yml` reads credentials from environment-variable interpolation and should not contain hardcoded secrets.

Required settings for startup validation:

- `IBKR_FLEX_TOKEN`
- `IBKR_FLEX_QUERY_ID`

`ACCOUNT_ID` has a development fallback of `DEFAULT_ACCOUNT`, but deployments should
set it explicitly so ingestion and repair commands target the intended account.

Optional Flex retry strategy tuning settings:

- `IBKR_FLEX_INITIAL_WAIT_SECONDS` (default `5.0`)
- `IBKR_FLEX_RETRY_ATTEMPTS` (default `7`)
- `IBKR_FLEX_BACKOFF_BASE_SECONDS` (default `10.0`)
- `IBKR_FLEX_BACKOFF_MAX_SECONDS` (default `60.0`)
- `IBKR_FLEX_JITTER_MIN_MULTIPLIER` (default `0.5`)
- `IBKR_FLEX_JITTER_MAX_MULTIPLIER` (default `1.5`)

Retry behavior uses exponential backoff with jitter and preserves IBKR code-specific retry floors for `1009`, `1018`, and `1019`.

If required settings are missing or invalid, startup fails with actionable validation output.

## Schema and migrations baseline (Task 2)

Task 2 introduces a full MVP schema baseline and migration workflow.

Included baseline tables:

- `instrument`, `label`, `instrument_label`, `note`
- `ingestion_run`, `raw_record`
- `event_trade_fill`, `event_cashflow`, `event_fx`, `event_corp_action`
- `position_lot`, `pnl_snapshot_daily`

Key implementation decisions:

- Full column-level MVP schema is implemented in Task 2 (no partial placeholder schema).
- UUID primary keys are database-generated with PostgreSQL `gen_random_uuid()`.
- Canonical event natural-key constraints follow `MVP_spec_freeze.md` names and contracts.

Migration files and configuration:

- `alembic.ini`
- `alembic/env.py`
- `alembic/versions/20260214_01_task2_mvp_schema_baseline.py`

Run migrations:

```bash
alembic upgrade head
```

Migration usage details:

- `docs/migrations.md`
- The incremental-ingestion indexes use ordinary transactional `CREATE INDEX`.
  Apply these migrations in a maintenance window when the tables are materially
  larger than the current dataset because writes can be blocked while each index
  is built.

## Ingestion orchestration baseline (Task 3)

Task 3 introduces ingestion orchestration with deterministic run lifecycle and preflight validation.

Included behavior:

- Single active ingestion run lock with rejection response `409` and message `run already active`
- Deterministic ingestion stages (`request` -> `poll` -> `download` -> `persist`)
- Typed Flex adapter failure classification with deterministic ingestion error codes for token/request/statement failures
- Required Flex section preflight with deterministic diagnostic code `MISSING_REQUIRED_SECTION`
- Structured stage timeline persisted in `ingestion_run.diagnostics` as a JSON array
- Trigger surfaces for both API and CLI

Operational note for live IBKR runs:

- If ingestion fails with `MISSING_REQUIRED_SECTION`, update the IBKR Flex query configuration to include the missing sections, then re-run ingestion.
- During assisted troubleshooting, the operator should be asked to add missing sections in IBKR query settings before retrying.

### Recovering a stale `started` ingestion run

Use this manual procedure only after an abrupt process death. There is no
automatic timeout or lease that marks an ingestion run failed.

1. Confirm that no app, CLI ingestion command, scheduler, or other worker is
   still executing the affected account/run. Stop the worker first if there is
   any uncertainty.
2. Back up PostgreSQL before changing run state:

   ```bash
   docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > stock_app_before_stale_run.dump
   ```

3. Inspect and copy the exact `ingestion_run_id`; confirm that its current
   status is still `started`:

   ```sql
   SELECT ingestion_run_id, account_id, started_at_utc, status
   FROM ingestion_run
   WHERE ingestion_run_id = '<exact-run-uuid>'::uuid;
   ```

4. Mark only that exact run failed in one transaction:

   ```sql
   BEGIN;
   UPDATE ingestion_run
   SET status = 'failed',
       ended_at_utc = now(),
       duration_ms = GREATEST(
           0,
           CAST(EXTRACT(EPOCH FROM (now() - started_at_utc)) * 1000 AS BIGINT)
       ),
       error_code = 'INGESTION_OPERATOR_RECOVERY',
       error_message = 'Marked failed after confirmed abrupt process termination'
   WHERE ingestion_run_id = '<exact-run-uuid>'::uuid
     AND status = 'started'
   RETURNING ingestion_run_id, account_id, status, ended_at_utc;
   COMMIT;
   ```

Do not bulk-update `started` runs, and do not infer staleness from elapsed time
alone.

API endpoints:

- `POST /ingestion/run`
- `POST /ingestion/reprocess`
- `GET /ingestion/runs`
- `GET /ingestion/runs/{ingestion_run_id}`
- `GET /ingestion/runs/{ingestion_run_id}/missing-sections`

CLI trigger command:

```bash
/stock_app/.venv/bin/python -m app.main ingestion-run
```

## Immutable raw persistence baseline (Task 4)

Task 4 replaces the Task 3 persist placeholder with immutable raw artifact and raw row persistence.

Included behavior:

- Dedicated immutable `raw_artifact` persistence with dedupe key `account_id + period_key + flex_query_id + payload_sha256`
- Raw section-row extraction persisted into `raw_record` for all detected sections (including non-MVP-mapped sections)
- Raw row provenance linked through `raw_record.raw_artifact_id -> raw_artifact.raw_artifact_id`
- Persist-stage diagnostics now include `payload_sha256`, `raw_artifact_id`, artifact dedupe flag, and inserted/deduplicated raw row counts
- A duplicate artifact takes the semantic no-op fast path only when
  `completed_ingestion_run_id` references an ingestion run persisted as
  `success`; otherwise ingestion recovers and processes that artifact's rows

Migration files and configuration additions:

- `alembic/versions/20260214_02_task4_raw_artifact_persistence.py`
- `alembic/versions/20260821_05_raw_artifact_completion.py`

Task 4 implementation modules:

- `app/db/raw_persistence.py`
- `app/jobs/raw_extraction.py`
- `app/jobs/ingestion_orchestrator.py`

## Canonical mapping and reprocess baseline (Task 5)

Task 5 implements deterministic canonical mapping from immutable raw rows and adds replay/reprocess trigger surfaces.

Included behavior:

- Canonical mapping service for `Trades`, `CashTransactions`, `ConversionRates`, and `CorporateActions` with fail-fast contract validation
- Conid-first instrument upsert before event upserts so canonical event rows resolve deterministic `instrument_id`
- Canonical UPSERT persistence for `event_trade_fill`, `event_cashflow`, `event_fx`, and `event_corp_action` using frozen natural keys and collision policies
- Normal distinct ingestion runs `canonical_mapping` after raw persistence using
  changed current-run rows; incomplete duplicate recovery reads the immutable
  artifact's complete row set
- Successfully completed duplicate artifacts are canonical no-ops; incomplete
  duplicate attempts replay semantic work before they can establish completion
- Deterministic reprocess workflow replays canonical mapping from `raw_record` only, without adapter request/poll/download
- Reprocess trigger surfaces exposed through both API and CLI

API endpoint additions:

- `POST /ingestion/reprocess`

Reprocess explicit scope query parameters:

- `period_key` (required when explicit scope is provided; format `YYYY-MM-DD`)
- `flex_query_id` (required when explicit scope is provided)

Example:

```bash
curl -X POST "http://127.0.0.1:8000/ingestion/reprocess?period_key=2026-02-14&flex_query_id=query"
```

The HTTP endpoint is replay-only. Supplying an explicit scope does not authorize
deletion of unsupported snapshot dates.

Ingestion run list/detail payload additions:

- `canonical_input_row_count`: Number of raw rows considered by canonical mapping for this run.
- `canonical_duration_ms`: Canonical stage duration in milliseconds.
- `canonical_skip_reason`: Optional reason when canonical mapping is skipped (for example `no_new_raw_rows_for_run`).

Ingestion diagnostics timeline additions:

- Poll retry events can include `download` stage entries with `status=retrying` and retry metadata (`poll_attempt`, `error_code`, `error_message`, `retry_after_seconds`).
- Timeout failures are surfaced through run failure diagnostics with `error_type=TimeoutError` and the propagated error message.

### Incremental ingestion diagnostics

Normal ingestion keeps every distinct Flex artifact and its raw rows. An exact
duplicate artifact skips raw-row insertion, canonical mapping, and snapshot
rebuilding only when that artifact has a successfully completed prior processing
run. A distinct artifact canonicalizes only rows changed from its latest
successfully processed source version and rebuilds snapshots only for affected
instruments and FX source currencies. Explicit reprocess commands remain full
replays.

Run-detail diagnostics include request transport, polling, cumulative poll wait,
preflight, XML extraction, artifact persistence, raw persistence, canonical raw
read, canonical mapping/persistence, snapshot, and total run durations in integer
milliseconds. Exact-duplicate skips and full-fallback stages include their
reason. An empty affected scope is reported as `snapshot_scope_mode="skipped"`;
it does not carry a separate skip reason.

CLI trigger command additions:

```bash
/stock_app/.venv/bin/python -m app.main reprocess-run
```

The command above is also replay-only. The operator-only cleanup path requires both
`--period-key` and `--flex-query-id`, a verified backup, and the candidate checks in
`docs/operations.md`:

```bash
/stock_app/.venv/bin/python -m app.main reprocess-run \
  --period-key 2026-02-14 \
  --flex-query-id query
```

Task 5 implementation modules:

- `app/mapping/service.py`
- `app/db/canonical_persistence.py`
- `app/jobs/canonical_pipeline.py`
- `app/jobs/reprocess_orchestrator.py`

## Valuation and FX fallback engine (Task 6)

Snapshot accounting uses broker authority when a completed `OpenPositions` artifact
is available and deterministic fallbacks outside that authority:

- With matching FIFO and broker quantities, economic unrealized P&L is broker position
  quantity x `OpenPositions.markPrice` x contract multiplier x FX, less FIFO cost basis.
  Missing mark, positive multiplier, or FX makes the row provisional; broker-reported
  unrealized P&L is not substituted for an exact match.
- Same-day `Trades.closePrice` and last known trade price are fallback marks only when
  completed broker-position authority is not being applied.
- Execution FX: `Trades.fxRateToBase` -> derived net-cash ratio -> exact/nearest-previous `ConversionRates`
- Base-currency events use `1.0`; missing non-base FX marks the snapshot provisional rather than labeling native amounts as USD
- Flex statement `reportDate` drives the snapshot business date, including delayed imports

Completed `OpenPositions` data is authoritative for daily snapshot quantity. Event-derived
FIFO lots remain independently auditable and can temporarily disagree with the broker;
quantity mismatches, broker-only positions, and missing valuation or FX inputs mark the
affected snapshot row provisional. Execution-level assignment and exercise (BookTrade)
rows without `ibExecID` use stable namespaced identities such as `FLEX_TXN:<transactionID>`
or `FLEX_TRADE:<tradeID>`.

Every reprocess reads immutable artifacts, replays their actual report dates
chronologically, and rebuilds canonical events and snapshots without requesting a new
IBKR Flex statement. The ordinary HTTP endpoint, including explicit HTTP scopes, never
deletes snapshots. Only the CLI command with both scope flags may remove unsupported
derived snapshot dates in that exact account/period/query scope. Back up and verify
PostgreSQL before using that cleanup-capable command; see `docs/operations.md`.

## FIFO ledger and daily snapshots (Task 7)

Task 7 adds the project-native FIFO ledger computation flow and persists daily snapshot outputs.

Included behavior:

- Deterministic FIFO lot matching for canonical trades with stable tie-break ordering
  (`trade_timestamp_utc` then source row id), including validated contract multipliers
  for option and other multiplier-bearing executions
- Base-currency realized and unrealized PnL computation with cashflow, fee, and withholding impacts
- Automatic snapshot stage execution after successful ingestion runs (`snapshot` timeline stage persisted in run diagnostics)
- Day-level snapshot persistence into `pnl_snapshot_daily` and reconciled open-lot persistence into `position_lot`
- Stale open lots are closed when deterministic replay no longer produces them
- UTC timestamps are retained while Flex statement dates drive daily snapshot boundaries

API endpoint additions:

- `GET /snapshots/daily`

Snapshot list query parameters:

- Pagination: `limit`, `offset`
- Sort: `sort_by` in (`report_date_local`, `instrument_id`, `total_pnl`, `created_at_utc`), `sort_dir` in (`asc`, `desc`)
- Date filters: `report_date_from`, `report_date_to` (inclusive, `YYYY-MM-DD`)

Task 7 implementation modules:

- `app/ledger/fifo_engine.py`
- `app/ledger/snapshot_dates.py`
- `app/ledger/snapshot_service.py`
- `app/db/ledger_snapshot.py`
- `app/api/routers/snapshot.py`
- `app/jobs/ingestion_orchestrator.py`
- `tests/test_ledger_fifo_snapshot.py`
- `tests/test_api_snapshot.py`

## Corporate-action review workflow (Task 8)

Corporate actions are classified using the frozen automatic/manual policy. Deterministic
actions such as complete split records can update the ledger automatically; ambiguous or
unsupported actions create manual cases and mark only affected instruments provisional.

Operator endpoints:

- `GET /corporate-actions/cases` lists cases and accepts a status filter.
- `PATCH /corporate-actions/cases/{case_id}` records status, owner, and resolution notes.

Resolving a case recomputes provisional state without hiding unrelated instruments or
reports. The API and persistence workflow are implemented in
`app/api/routers/corporate_actions.py`, `app/domain/corporate_actions.py`, and
`app/db/portfolio.py`.

## Labels and notes (Task 9)

Portfolio metadata APIs support label CRUD, many-to-many instrument assignment, and notes
attached to instruments or labels:

- `GET|POST /labels`, `PATCH|DELETE /labels/{label_id}`
- `POST|DELETE /instruments/{instrument_id}/labels/{label_id}`
- `GET|POST /notes`, `PATCH|DELETE /notes/{note_id}`

List endpoints enforce the configured pagination bounds and their documented sort/filter
allowlists. See the generated OpenAPI documentation for request and response schemas.

## Reporting and provenance (Task 10)

The reporting API exposes:

- `GET /reports/pnl/by-instrument`
- `GET /reports/pnl/by-label`
- `GET /reports/provenance`

PnL endpoints return JSON by default. Pass `format=csv` for the stable CSV `v1` contract;
responses include the fixed column order and `X-Schema-Version: v1`. Provenance rows link
reported values to canonical events, raw-record identities, section names, and immutable
source payloads.

## Reconciliation diff mode (Task 11)

`GET /reports/reconciliation/diff` compares broker-aligned and economic values using the
frozen tolerance matrix in `MVP_spec_freeze.md`. JSON and `format=csv` outputs include
absolute/relative differences, tolerances, pass/fail state, provisional state, and source
identities. Requests fail clearly when required broker reconciliation sections are absent.

## Operations, alerts, and recovery (Task 12)

`GET /operations/slo` and `/ui` expose the 30-day scheduled-ingestion SLO state. The
`alerts-evaluate` CLI sends transition-only alert and recovery notifications through
independently optional JSON webhook and SMTP channels, with durable per-destination
deduplication.

Checked-in systemd timers schedule:

- daily verified PostgreSQL backups;
- daily 60-day diagnostics retention;
- weekly isolated restore drills;
- daily ingestion; and
- SLO alert evaluation every 15 minutes.

Installation, environment variables, manual commands, locking, alert semantics, backup
retention, PITR, and incident recovery are documented in `deploy/systemd/README.md` and
`docs/operations.md`.

## Release quality gate (Task 13)

The release gate combines deterministic unit/regression fixtures with PostgreSQL-backed
seeded ingestion, replay, reporting, provenance, and reconciliation scenarios. Run:

```bash
IBKR_FLEX_TOKEN=test IBKR_FLEX_QUERY_ID=test .venv/bin/pytest -q
.venv/bin/ruff check app tests
.venv/bin/mypy
```

Operational release proof—including backup checksums, replay run IDs, reconciliation and
provisional results, migration state, and measured RPO/RTO—is recorded in
`docs/releases/2026-08-22-release-evidence.md`.

## VS Code virtual environment setup

This workspace is configured to automatically use the project virtual environment.

- Interpreter path: `${workspaceFolder}/.venv/bin/python`
- Python terminal environment activation: enabled

Configuration file:

- `.vscode/settings.json`

If VS Code was already open when this was configured, run **Developer: Reload Window** once.
