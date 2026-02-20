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
- Computes stock positions and P&L with a stocks-first ledger (FIFO in MVP).
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

- Options lifecycle accounting
- Real-time market data and risk dashboards
- Trade execution automation

## Architecture summary

- Runtime: Ubuntu LXC deployment
- Services: app + PostgreSQL (Docker Compose)
- Scheduler: cron-triggered ingestion CLI
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

Service endpoints:

- App: `http://127.0.0.1:8000`
- Health: `http://127.0.0.1:8000/health`
- PostgreSQL host port: `5433` (container port `5432`)

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

- `ACCOUNT_ID`
- `IBKR_FLEX_TOKEN`
- `IBKR_FLEX_QUERY_ID`

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
- Duplicate artifact ingest still finalizes run as `success` with explicit dedupe/no-op diagnostics

Migration files and configuration additions:

- `alembic/versions/20260214_02_task4_raw_artifact_persistence.py`

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
- Ingestion workflow runs `canonical_mapping` stage after raw persistence using current `ingestion_run_id` raw rows only (run-scoped processing)
- Duplicate raw payload retries are canonical no-op for ingestion when no new run-scoped raw rows are inserted; diagnostics include `canonical_skip_reason=no_new_raw_rows_for_run`
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

Ingestion run list/detail payload additions:

- `canonical_input_row_count`: Number of raw rows considered by canonical mapping for this run.
- `canonical_duration_ms`: Canonical stage duration in milliseconds.
- `canonical_skip_reason`: Optional reason when canonical mapping is skipped (for example `no_new_raw_rows_for_run`).

Ingestion diagnostics timeline additions:

- Poll retry events can include `download` stage entries with `status=retrying` and retry metadata (`poll_attempt`, `error_code`, `error_message`, `retry_after_seconds`).
- Timeout failures are surfaced through run failure diagnostics with `error_type=TimeoutError` and the propagated error message.

CLI trigger command additions:

```bash
/stock_app/.venv/bin/python -m app.main reprocess-run
```

Task 5 implementation modules:

- `app/mapping/service.py`
- `app/db/canonical_persistence.py`
- `app/jobs/canonical_pipeline.py`
- `app/jobs/reprocess_orchestrator.py`

## VS Code virtual environment setup

This workspace is configured to automatically use the project virtual environment.

- Interpreter path: `${workspaceFolder}/.venv/bin/python`
- Python terminal environment activation: enabled

Configuration file:

- `.vscode/settings.json`

If VS Code was already open when this was configured, run **Developer: Reload Window** once.
