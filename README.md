# ibkr-flex-ledger

IBKR Flex Ledger

Self-hosted portfolio accounting and analytics app for Interactive Brokers (IBKR) Flex data, focused on auditable, reconciliable stock portfolio metrics.

## Implementation policy (important)

The codebase must be structured in a strongly modular way from the start because additional functionality will be added later.

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

## VS Code virtual environment setup

This workspace is configured to automatically use the project virtual environment.

- Interpreter path: `${workspaceFolder}/.venv/bin/python`
- Python terminal environment activation: enabled

Configuration file:

- `.vscode/settings.json`

If VS Code was already open when this was configured, run **Developer: Reload Window** once.
