# MVP Implementation Task List (Outcome-Ordered)

Date: 2026-02-14
Source: MVP.md + MVP_spec_freeze.md

## Rules this task list follows
- Each task is a logically complete outcome.
- Tasks are ordered for real implementation flow.
- Dependencies are explicit and one-way.
- No overlap between task outcomes.
- Every task has tangible deliverables and user-verifiable acceptance criteria.

---

## Task 1 - Runtime Foundation and Module Boundaries
**Depends on:** None

**Outcome achieved**
- A runnable project skeleton with stable module boundaries for adapters, mapping, ledger, analytics, API/UI, jobs, and db access layer.

**Tangible deliverables**
- App bootstraps via one command in Docker Compose (`app` + `postgres`).
- Initial directory/module structure with db-layer boundary enforced by architecture conventions.
- Health endpoint that verifies app and database connectivity.
- Configuration model for Flex credentials and runtime settings.

**Acceptance criteria (user-verifiable)**
- `docker compose up` starts app and Postgres successfully.
- Health endpoint returns success and includes DB connectivity check.
- Code review confirms no direct SQL/ORM usage outside db modules.

---

## Task 2 - MVP Schema and Migration Baseline
**Depends on:** Task 1

**Outcome achieved**
- Persistent data model exists for all MVP entities and can be recreated from migrations.

**Tangible deliverables**
- Migration set creating MVP tables: instrument, label, instrument_label, note, ingestion_run, raw_record, event_trade_fill, event_cashflow, event_fx, event_corp_action, position_lot, pnl_snapshot_daily.
- Required indexes/constraints for canonical natural keys per frozen spec.
- Provenance fields linking canonical events back to ingestion runs and raw records.

**Acceptance criteria (user-verifiable)**
- Fresh database applies all migrations without manual edits.
- Re-running migrations is idempotent.
- Natural-key constraints match frozen definitions in MVP_spec_freeze.md.

---

## Task 3 - Ingestion Orchestration with Run Lock and Section Preflight
**Depends on:** Task 2

**Outcome achieved**
- Deterministic ingestion workflow exists with operational controls and failure semantics.

**Tangible deliverables**
- Ingestion command implementing `request -> poll -> download -> persist` run stages.
- Single active run lock preventing overlap.
- Required-section validation against frozen section matrix.
- Structured ingestion run lifecycle state transitions (`started/success/failed`).

**Acceptance criteria (user-verifiable)**
- Triggering two runs concurrently returns `409 run already active` for one request.
- Missing hard-required section fails run with `MISSING_REQUIRED_SECTION` and section names.
- Run detail view/API shows stage timeline and final status.

---

## Task 4 - Immutable Raw Artifact and Raw Row Persistence
**Depends on:** Task 3

**Outcome achieved**
- Raw Flex inputs are stored immutably and deduplicated by frozen idempotency policy.

**Tangible deliverables**
- Raw payload storage keyed by `period_key + flex_query_id + sha256`.
- Raw-record extraction into `raw_record` with source identifiers.
- Ingestion diagnostics persisted for failed and successful runs.

**Acceptance criteria (user-verifiable)**
- Re-ingesting identical report does not create duplicate raw artifacts.
- Raw records preserve source trace fields (section, row references).
- Failed run preserves actionable diagnostics linked to `run_id`.

---

## Task 5 - Canonical Mapping Pipeline with Deterministic UPSERT Keys
**Depends on:** Task 4

**Outcome achieved**
- Canonical event layer is produced deterministically from raw inputs.

**Tangible deliverables**
- Mappers for `trade_fill`, `cashflow`, `fx`, and `corp_action`.
- `conid`-first instrument identity mapping with alias persistence.
- UPSERT behavior using frozen natural keys and collision rules.
- Reprocess command that rebuilds canonical events from raw only.

**Acceptance criteria (user-verifiable)**
- Reprocess of same raw inputs reproduces identical canonical identities.
- No duplicate business events across reruns.
- Contract violations fail with structured field-level diagnostics.

---

## Task 6 - Valuation and FX Fallback Engine (Frozen Hierarchies)
**Depends on:** Task 5

**Outcome achieved**
- EOD mark and economic FX are calculated with deterministic fallback and diagnostics.

**Tangible deliverables**
- EOD mark fallback implementation (OpenPositions -> Trades.closePrice -> last Trades.tradePrice).
- Execution FX fallback implementation (Trades.fxRateToBase -> netCash ratio -> ConversionRates exact/nearest-previous).
- Deterministic tie-break rules and provisional diagnostics codes.

**Acceptance criteria (user-verifiable)**
- Fixture tests confirm same inputs always select same fallback source.
- Missing-all-sources behavior emits `EOD_MARK_MISSING_ALL_SOURCES` or `FX_RATE_MISSING_ALL_SOURCES`.
- Non-base currency FX without source marks outputs provisional.

---

## Task 7 - Stocks-First FIFO Ledger and Daily Snapshot Outputs
**Depends on:** Task 6

**Outcome achieved**
- Stock positions and P&L are computed and stored for reporting.

**Tangible deliverables**
- FIFO lot engine with realized/unrealized P&L.
- Economic P&L including fees and withholding impact.
- Daily snapshot generation into `pnl_snapshot_daily`.
- Time-boundary handling: UTC storage with Asia/Jerusalem business-date boundaries.

**Acceptance criteria (user-verifiable)**
- Known partial-close fixtures produce expected FIFO realized P&L.
- Open positions produce expected unrealized P&L with fallback valuation.
- Date-range snapshot queries return deterministic results across DST boundaries.

---

## Task 8 - Corporate-Action Manual Case Workflow and Provisional Scoping
**Depends on:** Task 7

**Outcome achieved**
- Deterministic corporate actions auto-process; ambiguous actions become tracked manual cases.

**Tangible deliverables**
- Auto/auto-if-unambiguous/manual handling per frozen allowlist.
- Manual-case records with required fields (`case_id`, status, owner, resolution metadata).
- Provisional-state propagation limited to affected instruments.

**Acceptance criteria (user-verifiable)**
- Non-allowlisted/ambiguous events always create manual cases.
- Affected instruments are marked provisional until resolution.
- Unrelated instruments remain reportable while manual backlog exists.

---

## Task 9 - Labels and Notes Domain Workflows
**Depends on:** Task 7

**Outcome achieved**
- Users can organize instruments and annotate portfolio context.

**Tangible deliverables**
- Label CRUD and instrument-label assignment endpoints.
- Notes CRUD/list endpoints with filter and sort behavior per frozen API list contract.

**Acceptance criteria (user-verifiable)**
- One instrument can hold multiple labels.
- Label and note list endpoints enforce allowed sort/filter fields and pagination envelope.
- Invalid pagination/sort inputs return frozen validation error codes.

---

## Task 10 - Reporting APIs with Provenance and CSV v1 Contracts
**Depends on:** Tasks 7, 8, 9

**Outcome achieved**
- MVP reporting outputs are available with full traceability and stable exports.

**Tangible deliverables**
- `GET /reports/pnl/by-instrument`, `GET /reports/pnl/by-label`, `GET /reports/provenance`.
- CSV `v1` export for by-instrument and by-label using frozen column order/types/nullability.
- Drilldown links from report row -> canonical event -> raw record.

**Acceptance criteria (user-verifiable)**
- Report totals reconcile with snapshot/ledger source data.
- CSV export schema exactly matches v1 contract and order.
- Every displayed metric can be traced to source event and raw row.

---

## Task 11 - Reconciliation Diff Mode with Frozen Tolerance Matrix
**Depends on:** Tasks 7, 8, 10

**Outcome achieved**
- Broker-aligned vs economic values are compared with explicit mismatch logic.

**Tangible deliverables**
- Reconciliation diff engine with per-metric tolerance matrix and formula context.
- `GET /reports/reconciliation/diff` API and CSV `v1` export.
- Provisional flags and unresolved counters carried into diff outputs.

**Acceptance criteria (user-verifiable)**
- Threshold edge cases pass/fail exactly at frozen tolerance boundaries.
- Diff outputs include `abs_diff`, `rel_diff`, `tolerance_abs`, `tolerance_rel`, `within_tolerance`, and provenance fields.
- UI/API labels use the same matrix values as backend calculations.

---

## Task 12 - Operations UX, SLO Monitoring, and Retention/Recovery Runbooks
**Depends on:** Tasks 3, 4, 11

**Outcome achieved**
- MVP is operationally supportable with diagnostics visibility and reliability controls.

**Tangible deliverables**
- Ingestion runs diagnostics UX/API (status, duration, timeline, error payload, retry metadata).
- SLO measurement and alert wiring for success rate, duration, and RTO thresholds.
- Derived diagnostics retention jobs (60 days hot + archive policy).
- Backup/PITR runbook and restore drill workflow.

**Acceptance criteria (user-verifiable)**
- Failed run shows human-readable summary plus structured diagnostic payload by `run_id`.
- SLO dashboards/metrics demonstrate target tracking and alert trigger behavior.
- Retention jobs archive then purge derived diagnostics without deleting immutable raw payloads.
- Restore drill evidence demonstrates RPO <= 15m and RTO <= 4h targets.

---

## Task 13 - End-to-End Quality Gate and Release Readiness
**Depends on:** Tasks 1-12

**Outcome achieved**
- MVP implementation is validated as deployable and auditable.

**Tangible deliverables**
- End-to-end seeded scenario from ingestion to reconciliation.
- Regression fixtures for deterministic reprocess, fallback behavior, and CSV contracts.
- Passing lint/static/test gates per project standards.

**Acceptance criteria (user-verifiable)**
- Full test suite passes, including integration flow and deterministic replay checks.
- `ruff` and `pylint` pass with zero new errors.
- Seeded walkthrough demonstrates full provenance chain and reconciliation diagnostics.
