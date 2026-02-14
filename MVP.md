# MVP Implementation Plan — IBKR Trade Ledger & Portfolio Insights

## Mandatory implementation rule: modular now, features later

This MVP must be implemented with a modular structure that supports future expansion with minimal changes to already working parts.

Rules:

1. Build stable module boundaries now (adapters, mapping, ledger, analytics, API/UI, jobs).
2. Implement only MVP behavior now. Do not implement phase 2+ functionality described in `max_plan.md`.
3. Keep extension points explicit so later domains can be added as new modules, not rewrites.
4. Avoid cross-module coupling that forces edits across working MVP modules when adding options, strategy, performance, or advanced corporate actions.
5. Preserve the raw-first and deterministic recompute model so future features can layer on top safely.
6. All database operations must exist only in the database layer. No direct SQL or ORM queries are allowed from adapters, services, API routers, CLI commands, jobs, or domain modules.
7. Before implementing any feature or significant bug fix, scan `references/REFERENCE_NOTES.md` and explicitly reuse applicable patterns instead of duplicating established solutions.

`max_plan.md` is a reference for target architecture and boundary design, not a scope expansion for initial delivery.
`references/REFERENCE_NOTES.md` is the working catalog for external reference projects and approved reuse patterns.

## Feature Summary (Implemented in MVP)

1. **Automated IBKR Flex ingestion**: scheduled daily pull, immutable raw payload storage, normalized record persistence, and ingestion run audit trail.
2. **Canonical event pipeline**: transformation of raw records into stock trades, cashflows (dividends, fees, withholding tax), FX events, and flagged corporate actions.
3. **Stocks-first portfolio engine**: FIFO lot tracking, current positions, realized and unrealized P&L, and base-currency reporting.
4. **Labels and notes workflow**: label CRUD, many-to-many label assignment to instruments, notes on instruments, and optional event notes.
5. **Reporting and drilldowns**: portfolio and instrument views plus P&L by label with date-range filters and traceability to source rows.
6. **Reconciliation mode**: side-by-side comparison of computed values vs broker-oriented view, with diff visibility by day and instrument.
7. **Operational reliability**: strict validation for schema drift, replay/reprocess capability from raw data, deterministic jobs, and diagnostics for failed runs.

Specification reference:
- `MVP_spec_freeze.md` is the authoritative source for frozen MVP implementation values (natural keys, fallback rules, tolerances, SLOs, CSV contracts, retention windows).

---

## 1. Objective and Scope

Build a self-hosted web application that imports IBKR activity using Flex Web Service, computes auditable portfolio metrics for stocks, and provides analysis via labels and notes.

### In Scope
- Daily Flex report ingestion and persistence
- Event normalization pipeline
- Stocks-only positions and P&L
- Labels, notes, and grouped reporting
- Reconciliation and traceability workflows
- Single IBKR account operation in MVP

### Out of Scope (MVP)
- Options lifecycle accounting
- Strategy grouping and strategy lifecycle linking
- Advanced performance analytics (MWR/TWR, deep FX attribution)
- Real-time market data and risk dashboards
- Trade execution automation
- Full automatic coverage of all corporate-action edge cases
- Multi-account ingestion, storage isolation, and account-level reporting

### Scope boundary from max plan
- The structure must be ready for phase 2+ modules.
- Phase 2+ logic must not be implemented in MVP code paths.
- New domains should be add-on modules that integrate through existing public interfaces.

---

## 2. MVP Architecture

### Runtime
- Ubuntu LXC deployment
- Docker Compose services: app + PostgreSQL
- Reverse proxy provides authentication boundary
- Cron executes ingestion CLI
- Current environment baseline: PostgreSQL `17/main` is already installed and online on `5432`

### Logical Layers
1. **Adapter layer**: fetches Flex reports and persists immutable payloads.
2. **Mapping layer**: transforms raw records into canonical domain events.
3. **Ledger layer**: computes positions, lots, and P&L.
4. **Analytics layer**: aggregates by instrument and label.
5. **API/UI layer**: CRUD and reporting endpoints.
6. **Job layer**: ingestion, reprocess, and snapshot workflows.

### Modularity constraints
- Each layer exposes a narrow interface and avoids direct dependency on non-adjacent layers.
- Future domains from `max_plan.md` (options, strategies, corporate action workflows, performance) must be attachable as separate modules.
- Existing MVP modules should require minimal or no internal rewrites when phase 2+ modules are added.
- Schema and service naming should be domain-oriented to preserve discoverability as modules grow.
- Database access is centralized in the database layer (`db/*` modules). Other layers must call repository/data-access interfaces, not query the database directly.

### Core Principle
Raw input remains immutable. All derived data can be regenerated from raw records.

### Resolved MVP policy decisions
- Deployment policy: coordinated single-site deployments only; no backward-compatibility layers, old API support, feature flags, or field aliases in MVP changes.
- Idempotency policy: dedupe raw artifacts by `period_key + flex_query_id + sha256`, then UPSERT canonical records on stable natural keys.
- Instrument identity policy: `conid` is canonical for IBKR data; ISIN/CUSIP/FIGI and symbol/localSymbol are aliases with conid-first conflict handling.
- Parsing boundary policy: Flex parsing remains isolated from accounting and reporting logic; parser output is a mapping input contract, not a ledger dependency.
- Ingestion flow policy: execute deterministic `request -> poll -> download -> persist` stages with run status tracking and deterministic retry behavior.
- Ingestion overlap policy: enforce a single active ingestion run lock; reject overlapping triggers with `409` and message `run already active`.
- Time policy: store all timestamps in UTC; apply `Asia/Jerusalem` for UI rendering and business date boundaries (ingestion windows, daily snapshots, report filters).
- Base reporting currency policy: `USD` is the canonical base reporting currency in MVP and is immutable after initial setup.
- Single-account API contract policy: `account_id` is internal-only in MVP API payloads; backend repositories use one fixed configured account context, and external multi-account API exposure is post-MVP.
- Unrealized valuation policy (EOD mark fallback):
   1. `OpenPositions.markPrice` (same `conid` + `report_date`)
   2. `Trades.closePrice` (same `conid` + `report_date`)
   3. Last known `Trades.tradePrice` on or before `report_date`
   Tie-break: latest `dateTime`, then highest numeric `transactionID`, then highest raw record primary key. If all missing: mark provisional with `EOD_MARK_MISSING_ALL_SOURCES`.
- Economic FX policy (execution FX fallback):
   1. `Trades.fxRateToBase` (non-null and > 0)
   2. Derived `abs(netCashInBase) / abs(netCash)` when `netCash != 0`
   3. `ConversionRates.rate` for (`fromCurrency` -> base) on `report_date`, else nearest previous `reportDate`
   Tie-break: exact date over previous date; within date use latest `ingestion_run_id`, then highest raw record primary key. If all missing: allow `1.0` only for base-currency events; otherwise mark provisional with `FX_RATE_MISSING_ALL_SOURCES`.
- Reconciliation mismatch policy (frozen matrix):
   - `realized_pnl`, `unrealized_pnl`, `fees`, `withholding_tax`: abs `max(0.01, minor_unit)`, rel `0.0001`, currency precision (for MVP: `USD/EUR/ILS=2`, `JPY=0`; extend via ISO minor-unit mapping).
   - `position_qty`: abs `0.000001`, rel `0`, precision `6`.
   - Formula: `abs(a-b) <= abs_tol OR abs(a-b)/max(abs(b),1e-9) <= rel_tol`; `position_qty` uses absolute-only check.
- Corporate action policy (frozen boundary):
   - Auto (safe): `CD`, `CP`, `FA`, `IC`, CUSIP/ISIN alias changes.
   - Auto-if-unambiguous: `FS`, `FI`, `RS`, `SD`.
   - Always manual in MVP: `SO`, `CO`, `TC`, `OR`, `TI`, `RI`, `SR`, `DI`, `TO`, `BC`, `CI`, `HD`, `HI`, `CS`, `CC`, `CA`, and `DW`.
   - Scope rule: any event that requires elections, multi-leg interpretation, cost-basis allocation between instruments, or option deliverable adjustment is always manual in MVP.
- Manual resolution ownership: single-user operation; unresolved mandatory corporate-action cases mark affected outputs as provisional with visible warnings.
- Manual backlog behavior policy: unresolved manual corporate-action cases must not block unrelated instruments or global report generation; only affected instruments remain provisional with explicit unresolved counters.
- Security boundary: authentication is delegated to reverse proxy; no in-app authentication and no reverse-proxy identity header contract checks in MVP.
- Retention policy: keep immutable raw payloads indefinitely for audit; retain derived diagnostics for `60 days` hot retention, then archive before purge.
- Reliability baseline (frozen SLOs): ingestion success `>=99.0%` (rolling 30 days), ingestion duration `p95 <= 15 minutes` (rolling 14 days), RTO `<= 4 hours`; alerts at success `<98.0%`, any run `>30 minutes`, or unresolved incident at `2 hours`.
- Backup and restore policy (frozen): PostgreSQL full base backup every `24h`, WAL archive shipping every `5m`, PITR window `14 days`, backup retention (`14` daily, `8` weekly, `12` monthly), restore target `RPO <= 15m` and `RTO <= 4h`, with weekly restore smoke tests and monthly full restore drill.
- Missing required sections policy: mark ingestion run failed, preserve diagnostics, and block publishing incomplete downstream snapshots.
- Required Flex section matrix policy (frozen):
   - Hard-required for MVP publish (fail run if missing): `Trades`, `OpenPositions`, `CashTransactions`, `CorporateActions`, `ConversionRates`, `SecuritiesInfo`, `AccountInformation`.
   - Reconciliation-required in MVP (fail run when reconciliation mode is enabled and section is missing): `MTMPerformanceSummaryInBase`, `FIFOPerformanceSummaryInBase`.
   - Future-proof ingest-now, non-blocking if absent: `InterestAccruals`, `ChangeInDividendAccruals`, `OpenDividendAccruals`, `ChangeInNAV`, `StmtFunds`, `UnbundledCommissionDetails`.
   - Optional-but-ignored in MVP mapping: all other sections remain raw-only persistence until explicitly promoted.
- API list contract policy (frozen, configurable defaults):
   - Shared query params for list endpoints: `limit`, `offset`, `sort_by`, `sort_dir`.
   - Environment-configurable defaults: `API_DEFAULT_LIMIT=50`, `API_MAX_LIMIT=200`, `API_DEFAULT_SORT_DIR=asc`.
   - Validation: `limit < 1` -> `400 INVALID_PAGINATION`; `offset < 0` -> `400 INVALID_PAGINATION`; invalid `sort_by` -> `400 INVALID_SORT_FIELD`; invalid `sort_dir` -> `400 INVALID_SORT_DIRECTION`; `limit > API_MAX_LIMIT` is clamped and reported as `applied_limit`.
   - Endpoint sorting/filtering rules are code-level constants (not runtime-configurable) for deterministic behavior.
- Export baseline: provide CSV exports with schema version `v1` and fixed column contracts for `GET /reports/pnl/by-instrument`, `GET /reports/pnl/by-label`, and `GET /reports/reconciliation/diff`.
- Ingestion observability baseline: structured logs are mandatory with fields `run_id`, `stage`, `status`, `started_at_utc`, `ended_at_utc`, `duration_ms`, `source_section`, `source_record_ref`, `error_code`, `error_message`, `exception_type`, `stack_trace`, `retry_count`, `next_retry_at_utc`.
- Ingestion diagnostics UX baseline (MVP minimum): runs list with status/duration, run stage timeline, error panel, source trace panel, retry visibility, and one-click copy for `run_id` and structured error payload.

### Specification freeze required before implementation completion
- Canonical natural-key definitions per event type (`trade_fill`, `cashflow`, `fx`, `corp_action`) must be documented in one versioned mapping spec.
- EOD mark fallback and FX fallback must be documented as exact ordered source lists with deterministic tie-break rules.
- Corporate-action auto-handled allowlist must be explicitly enumerated.
- Reconciliation tolerance matrix must define concrete absolute and relative thresholds plus currency precision.
- Reliability SLOs must define concrete targets for ingestion success, run duration, and recovery time.
- CSV export contracts must define per-endpoint column names, order, and data types with schema version labels.
- Derived diagnostics retention must define concrete retention windows and archival trigger criteria.

Working artifact: `MVP_spec_freeze.md` is the authoritative template for these decisions.

---

## 3. Data Contract and Storage Plan

### Main Tables (MVP)
- `instrument` (stocks only)
- `label`
- `instrument_label`
- `note`
- `ingestion_run`
- `raw_record`
- `event_trade_fill`
- `event_cashflow`
- `event_fx`
- `event_corp_action`
- `position_lot`
- `pnl_snapshot_daily` (mandatory in MVP for deterministic day-level reporting and reconciliation)

### Required Traceability Fields
- Source report metadata (query id, report date, retrieval timestamp)
- Source identifiers per row (`ibkr_ref` or equivalent)
- Transformation provenance (`ingestion_run_id`, source record link)

### Canonical event contract requirements
- Define required fields per event type with strict enum values and nullability rules.
- Version parser/mapping contracts by Flex section so schema drift fails fast with clear diagnostics.
- Ensure deterministic canonical output identity so replay/reprocess yields stable record keys.
- Document and version natural-key UPSERT fields per canonical event type before enabling production reprocess runs.

### Validation Rules
- Reject ingestion when mandatory Flex sections are missing
- Emit deterministic diagnostic code `MISSING_REQUIRED_SECTION` with missing section names for preflight and runtime validation failures
- Fail transformation when required fields are absent or incompatible
- Persist structured diagnostics (record type, field, reason, sample source row)

---

## 4. Feature-Level Implementation Plan

## Milestone 0 — Foundation (Day 1-2)

### Deliverables
- Project skeleton and module boundaries
- Docker Compose with app and PostgreSQL
- Migration baseline for all MVP entities
- Configuration model for Flex credentials and runtime settings

### Acceptance Criteria
- Fresh environment boots with one command
- Database migrations apply cleanly
- Health endpoint verifies DB connectivity

---

## Milestone 1 — Ingestion and Raw Persistence (Day 3-5)

### Deliverables
- CLI command to fetch Flex report by configured query
- Ingestion run lifecycle: started/success/failed
- Immutable raw payload persistence
- Raw-row extraction into `raw_record`

### Acceptance Criteria
- A scheduled run can ingest one full report end-to-end
- Failed runs persist clear error diagnostics
- Re-ingesting the same period dedupes raw artifacts via `period_key + flex_query_id + sha256`
- Canonical records converge via UPSERT on stable natural keys without duplicate business events

---

## Milestone 2 — Canonical Event Mapping (Day 6-9)

### Deliverables
- Mappers for stock trades, fees, dividends, withholding tax, and FX
- Corporate action event capture with `requires_manual` flag
- Reprocess command: regenerate canonical events from raw records only
- Instrument identity mapper with `conid` canonicalization and alias persistence strategy

### Acceptance Criteria
- Canonical events generated with deterministic output
- Reprocess run reproduces same event set from same raw source
- Missing field or section produces hard-fail with actionable diagnostics
- Non-deterministic or ambiguous corporate-action inference opens manual case and blocks affected instrument recompute outputs

---

## Milestone 3 — Positions and P&L Engine (Day 10-14)

### Deliverables
- FIFO lot engine
- Position state per instrument (qty, average cost, cost basis)
- Realized and unrealized P&L computation
- Economic P&L includes fees and withholding effects

### Acceptance Criteria
- Closed trade sequences produce expected realized P&L
- Open lots produce expected unrealized P&L using IBKR end-of-day marks tied to report date, with the frozen EOD fallback hierarchy
- Daily snapshots persist and are queryable by date range
- Economic reporting uses broker-provided execution FX when present and otherwise uses the frozen FX fallback hierarchy
- Snapshot and report date boundaries are interpreted in `Asia/Jerusalem` and executed as UTC-boundary queries

---

## Milestone 4 — Labels, Notes, and Reporting (Day 15-18)

### Deliverables
- CRUD endpoints for labels and notes
- Instrument-label assignment management
- Reports:
  - P&L by label
  - Label -> instruments -> events drilldown
  - Date range and instrument filters

### Acceptance Criteria
- One instrument can hold multiple labels
- Grouped report totals equal instrument-level rollups
- Every report row can trace back to canonical events and then raw source

---

## Milestone 5 — Reconciliation and Audit UX (Day 19-22)

### Deliverables
- Reconciliation mode (broker-aligned calculations)
- Economic mode (full-cost perspective)
- Diff views by day and instrument
- Link-back path: report row -> event -> raw record
- Diff metadata that includes formula/rule context and tolerance basis

### Acceptance Criteria
- User can explain any displayed metric via full provenance chain
- Reconciliation mismatches are visible, not hidden, and evaluated using per-metric tolerance with currency-specific precision
- Diff output includes enough context for troubleshooting
- Outputs impacted by unresolved mandatory manual cases are clearly marked provisional with unresolved counters
- Tolerance values are sourced from one shared matrix used consistently by backend calculations, API payloads, and UI labels

---

## 5. API Surface (MVP Minimum)

### Ingestion / Operations
- `POST /ingestion/run`
- `POST /ingestion/reprocess/{ingestion_run_id}`
- `GET /ingestion/runs`
- `GET /ingestion/runs/{id}`

### Master Data
- `GET /instruments`
- `GET /instruments/{id}`
- `GET /labels`, `POST /labels`, `PATCH /labels/{id}`, `DELETE /labels/{id}`
- `POST /instruments/{id}/labels/{label_id}`
- `DELETE /instruments/{id}/labels/{label_id}`
- `POST /notes`, `GET /notes`

### List endpoint defaults and constraints
- `GET /instruments`
   - default sort: `symbol asc, instrument_id asc`
   - allowed `sort_by`: `symbol`, `conid`, `updated_at_utc`
   - filters: `label_id`, `search`, `active_only`
- `GET /notes`
   - default sort: `created_at_utc desc, note_id desc`
   - allowed `sort_by`: `created_at_utc`, `updated_at_utc`, `instrument_id`
   - filters: `instrument_id`, `label_id`, `created_from`, `created_to`
- `GET /ingestion/runs`
   - default sort: `started_at_utc desc, ingestion_run_id desc`
   - allowed `sort_by`: `started_at_utc`, `ended_at_utc`, `status`, `duration_ms`
   - filters: `status`, `from_utc`, `to_utc`, `run_type`

### List response envelope
- `items`: array
- `page`: `{limit, offset, returned, total, has_more, applied_limit}`
- `sort`: `{sort_by, sort_dir}`
- `filters`: normalized applied filters

### Reporting
- `GET /reports/pnl/by-instrument`
- `GET /reports/pnl/by-label`
- `GET /reports/reconciliation/diff`
- `GET /reports/provenance` (event/raw trace by report item)

### CSV export contracts (`v1`)
- Contract stability rule: column names, order, datatypes, and nullability are stable within `v1`; breaking changes require a new schema version.
- Validation rule: reject export payloads that violate `v1` column order or datatype/nullability contracts.
- `GET /reports/pnl/by-instrument` CSV columns in order:
   1. `report_date_local` (`date`, non-null)
   2. `instrument_id` (`uuid`, non-null)
   3. `conid` (`text`, non-null)
   4. `symbol` (`text`, non-null)
   5. `currency` (`text`, non-null)
   6. `position_qty` (`decimal(24,8)`, non-null)
   7. `cost_basis` (`decimal(24,8)`, nullable)
   8. `realized_pnl` (`decimal(24,8)`, non-null)
   9. `unrealized_pnl` (`decimal(24,8)`, non-null)
   10. `total_pnl` (`decimal(24,8)`, non-null)
   11. `provisional` (`boolean`, non-null)
- `GET /reports/pnl/by-label` CSV columns in order:
   1. `report_date_local` (`date`, non-null)
   2. `label_id` (`uuid`, non-null)
   3. `label_name` (`text`, non-null)
   4. `instrument_count` (`integer`, non-null)
   5. `realized_pnl` (`decimal(24,8)`, non-null)
   6. `unrealized_pnl` (`decimal(24,8)`, non-null)
   7. `total_pnl` (`decimal(24,8)`, non-null)
   8. `fees` (`decimal(24,8)`, non-null)
   9. `withholding_tax` (`decimal(24,8)`, non-null)
   10. `provisional` (`boolean`, non-null)
- `GET /reports/reconciliation/diff` CSV columns in order:
   1. `report_date_local` (`date`, non-null)
   2. `instrument_id` (`uuid`, non-null)
   3. `conid` (`text`, non-null)
   4. `symbol` (`text`, non-null)
   5. `metric` (`text`, non-null)
   6. `broker_value` (`decimal(24,8)`, nullable)
   7. `economic_value` (`decimal(24,8)`, nullable)
   8. `abs_diff` (`decimal(24,8)`, non-null)
   9. `rel_diff` (`decimal(24,8)`, nullable)
   10. `tolerance_abs` (`decimal(24,8)`, non-null)
   11. `tolerance_rel` (`decimal(24,8)`, non-null)
   12. `within_tolerance` (`boolean`, non-null)
   13. `formula_context` (`text`, nullable)
   14. `source_event_id` (`uuid`, nullable)
   15. `source_raw_record_id` (`uuid`, nullable)
   16. `provisional` (`boolean`, non-null)

---

## 6. Testing Strategy and Quality Gates

### Test Types
- **Unit tests**: parser adapters, mappers, lot math, and P&L calculations
- **Fixture regression tests**: golden Flex payloads and expected canonical outputs
- **Integration tests**: ingestion -> mapping -> ledger -> reporting flow

### Required Cases
- Fees and withholding included in economic calculations
- Partial closes under FIFO
- FX conversion event handling
- Schema drift detection and diagnostic quality
- Reprocess determinism from immutable raw inputs
- Time-boundary correctness across `Asia/Jerusalem` DST transitions for ingestion windows, snapshots, and report filters

### Completion Gates
- All MVP tests pass
- Linting and static checks pass
- One seeded dataset supports manual reconciliation walkthrough

---

## 7. Risk Register and Mitigation Plan

1. **Flex schema drift**
   - Mitigation: strict validation, versioned mappers, golden fixtures
2. **Reconciliation variance with broker summaries**
   - Mitigation: explicit mode separation, formula/rule metadata in diff output, and documented tolerance policy
3. **Corporate action complexity**
   - Mitigation: conservative automation boundary, deterministic flagging, and provisional-output warnings for unresolved mandatory cases
4. **Data replay correctness**
   - Mitigation: immutable raw storage plus deterministic reprocessing
5. **Operational reliability drift**
   - Mitigation: single active run lock with `409` on overlap, timeout and retry/backoff policy, and SLO-backed alert thresholds

---

## 8. Definition of Done (MVP)

MVP is complete when all conditions are true:
- Daily scheduled ingestion is operational and observable
- Stocks positions and P&L are available and stable
- Labels and notes support grouped analysis workflows
- Reconciliation and economic views are both available
- Every metric can be traced back to canonical and raw records
- Known edge cases are clearly flagged for manual handling

---

## 9. Post-MVP Roadmap (Not Included in This Delivery)

- Options lifecycle and strategy grouping
- Advanced performance analytics (MWR/TWR, deeper FX attribution)
- CSV fallback imports and richer exports
- Multi-account support
- Reverse-proxy authentication header/trust hardening and enforcement

Reference: `max_plan.md` defines the target end-state architecture and future domains.
