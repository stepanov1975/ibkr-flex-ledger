# MVP Specification Freeze Sheet

Date: 2026-02-14
Scope: IBKR Trade Ledger MVP
Status: Reference-informed baseline proposal

## Purpose

This document converts open MVP policy decisions into concrete, testable specifications.

Rules:
- Every section must be completed before MVP implementation completion.
- Values here are normative for code, tests, API responses, and UI labels.
- If a value changes, update this document and matching fixtures in the same change.

## Global Fixed Decisions (Already Final)

- MVP account scope: strictly single IBKR account.
- MVP single-account API contract: `account_id` remains internal-only in MVP API payloads; backend data-access layers use one fixed configured account context.
- MVP base reporting currency: `USD`, fixed at initial setup and immutable in MVP.
- Time policy: persist timestamps in UTC; apply `Asia/Jerusalem` for UI and business date boundaries.
- Ingestion overlap policy: single active run lock; reject overlapping triggers with `409` and message `run already active`.
- Authentication hardening for proxy headers/trust assumptions: out of MVP scope (post-MVP).
- Reverse-proxy identity header contract validation in application code: out of MVP scope (post-MVP).

## Reference basis used for this baseline

- `references/finx-reports-ib/ngv_reports_ibkr/unified_df.py` (execution-ID dedup and unified trade mapping)
- `references/finx-reports-ib/data/unified_trades.md` (schema contracts and UTC normalization pattern)
- `references/ibflex/ibflex/Types.py` (`Trade`, `OpenPosition`, and corporate-action identifiers such as `actionID`, `transactionID`, `ibExecID`)
- `references/ibflex/ibflex/enums.py` (cash action and corporate-action `Reorg` taxonomy)
- `references/flexquery/flexquery/transform_csv.py` (stable CSV field ordering and explicit fieldname contracts)

---

## 1) Canonical Event Natural Keys by Type

Goal: define exact UPSERT natural keys for deterministic replay and deduplication.

| Event Type | Natural Key Fields (ordered) | Uniqueness Constraint Name | Collision Handling Rule | Notes |
|---|---|---|---|---|
| `trade_fill` | `account_id`, `ib_exec_id` | `uq_event_trade_fill_account_exec` | If same key reappears, UPSERT mutable numeric fields (`commission`, `realized_pnl`, `net_cash`, `cost`) and keep earliest `ingestion_run_id` as origin. | Mirrors execution-level identity in references (`fill_execution_id`/`ibExecID`). |
| `cashflow` | `account_id`, `transaction_id`, `cash_action`, `currency` | `uq_event_cashflow_account_txn_action_ccy` | If duplicate key with same amount/date, ignore; if amount/date differs, mark as correction and keep latest `report_date`. | `transactionID` is present across IB Flex sections and is the primary anchor. |
| `fx` | `account_id`, `transaction_id`, `currency`, `functional_currency` | `uq_event_fx_account_txn_ccy_pair` | If duplicate key reappears, UPSERT computed fields and preserve first-seen source row pointer. | Uses transaction identity first, then currency pair for deterministic uniqueness. |
| `corp_action` | `account_id`, `action_id` | `uq_event_corp_action_account_action` | If `action_id` is null, fallback key is (`account_id`, `transaction_id`, `conid`, `report_date`, `reorg_code`); conflicts create mandatory manual case. | `actionID`/`transactionID` appear in ibflex corporate-action types. |

Acceptance checks:
- Reprocessing same raw input produces identical canonical row identities.
- Duplicate business events are not created across reruns.

---

## 2) Fallback Hierarchies (EOD Marks and Execution FX)

Goal: define deterministic ordered source lists and tie-break behavior.

### 2.1 EOD Mark Fallback

| Priority | Source | Eligibility Condition | Tie-Break Rule | Missing-Data Behavior |
|---|---|---|---|---|
| 1 | `OpenPositions.markPrice` | Matching `conid` and target `report_date` exists and `markPrice` is not null | Highest source priority always wins | If unavailable, continue to priority 2 |
| 2 | `Trades.closePrice` | At least one trade row for `conid` on target `report_date` with non-null `closePrice` | Choose row with latest `dateTime`; if tied, highest numeric `transactionID` | If unavailable, continue to priority 3 |
| 3 | Last execution `tradePrice` on or before target `report_date` | Trade exists for `conid` with non-null `tradePrice` | Latest `dateTime`; then highest numeric `transactionID`; then highest raw record primary key | Use value but mark valuation as provisional with diagnostic code `EOD_MARK_FALLBACK_LAST_TRADE` |

If all three sources are missing, valuation is marked provisional and diagnostic code `EOD_MARK_MISSING_ALL_SOURCES` is emitted.

### 2.2 Execution FX Fallback

| Priority | Source | Eligibility Condition | Tie-Break Rule | Missing-Data Behavior |
|---|---|---|---|---|
| 1 | `Trades.fxRateToBase` | Event row has non-null `fxRateToBase` and non-zero denominator context | Highest source priority always wins | If unavailable, continue to priority 2 |
| 2 | Derived from `netCashInBase / netCash` | Both values are present and `netCash != 0` | Round to 10 decimal places using half-even | If unavailable, continue to priority 3 |
| 3 | `ConversionRates` for (`currency`, base, `report_date`) | Matching pair exists for report date (or nearest previous available date) | Pick exact date first, otherwise nearest previous date; within same date pick latest `ingestion_run_id`, then highest raw record primary key | If unavailable, use `1.0` only when `currency == base`; otherwise set event provisional and block economic FX output |

If all three sources are missing for non-base currency events, emit diagnostic code `FX_RATE_MISSING_ALL_SOURCES`.

Acceptance checks:
- Same inputs always produce same valuation and economic FX outputs.
- Diagnostics explain which fallback source was applied.

---

## 3) Corporate Action Auto-Handle Allowlist

Goal: explicitly list which action types are auto-resolved vs mandatory manual.

| Action Type | Auto or Manual | Conditions for Auto | If Manual, Blocking Scope | Rationale |
|---|---|---|---|---|
| `FORWARDSPLIT (FS)` | Auto | One-to-one deterministic split ratio present | N/A | Deterministic quantity/cost basis transform |
| `REVERSESPLIT (RS)` | Auto | One-to-one deterministic reverse-split ratio present | N/A | Deterministic inverse quantity/cost basis transform |
| `STOCKDIV (SD)` | Auto | Deterministic stock dividend factor available | N/A | Deterministic lot adjustment |
| `CASHDIV (CD)` | Auto | Cash amount and withholding are explicit in source rows | N/A | Treated as cashflow with clear amount |
| `SPINOFF (SO)` | Manual | N/A | Block affected instrument recompute outputs | Requires discretionary cost-basis allocation |
| `MERGER (TC)` | Manual | N/A | Block affected instrument recompute outputs | Consideration mix and ratio ambiguity |
| `RIGHTSISSUE (RI/SR)` | Manual | N/A | Block affected instrument recompute outputs | Election/valuation ambiguity |
| `CHOICEDIV (CH/HD/HI)` | Manual | N/A | Block affected instrument recompute outputs | Election-based action, not deterministic |
| `GENERICVOLUNTARY (GV)` | Manual | N/A | Block affected instrument recompute outputs | Explicitly non-deterministic without user choice |

Manual workflow minimum fields:
- `case_id`
- `action_type`
- `instrument_id`
- `status` (`open`, `resolved`, `dismissed`)
- `owner`
- `resolution_note`
- `resolved_at_utc`

Acceptance checks:
- Any non-allowlisted corporate action becomes mandatory manual.
- Affected outputs are marked provisional until case resolution.
- Unresolved manual cases do not block unrelated instruments or global report generation; only affected instruments carry provisional status and unresolved counters.

---

## 4) Reconciliation Tolerance Matrix

Goal: define exact numeric thresholds and precision for mismatch evaluation.

| Metric | Currency | Absolute Threshold | Relative Threshold | Decimal Precision | Comparison Formula |
|---|---|---|---|---|---|
| `realized_pnl` | Per currency minor unit | `max(0.01, minor_unit)` | `0.0001` | Currency precision (`USD/EUR/ILS=2`, `JPY=0`) | `abs(a-b) <= abs_tol OR abs(a-b)/max(abs(b),1e-9) <= rel_tol` |
| `unrealized_pnl` | Per currency minor unit | `max(0.01, minor_unit)` | `0.0001` | Currency precision (`USD/EUR/ILS=2`, `JPY=0`) | `abs(a-b) <= abs_tol OR abs(a-b)/max(abs(b),1e-9) <= rel_tol` |
| `fees` | Per currency minor unit | `max(0.01, minor_unit)` | `0.0001` | Currency precision (`USD/EUR/ILS=2`, `JPY=0`) | `abs(a-b) <= abs_tol OR abs(a-b)/max(abs(b),1e-9) <= rel_tol` |
| `withholding_tax` | Per currency minor unit | `max(0.01, minor_unit)` | `0.0001` | Currency precision (`USD/EUR/ILS=2`, `JPY=0`) | `abs(a-b) <= abs_tol OR abs(a-b)/max(abs(b),1e-9) <= rel_tol` |
| `position_qty` | N/A | `0.000001` | `0` | 6 | `abs(a-b) <= 0.000001` |

Acceptance checks:
- Backend diff engine and UI use the same matrix values.
- Tests verify pass/fail boundary behavior at threshold edges.

---

## 5) Reliability SLO Targets

Goal: define measurable MVP reliability targets and alert thresholds.

| SLO Metric | Target | Measurement Window | Alert Threshold | Owner | Remediation Trigger |
|---|---|---|---|---|---|
| Ingestion success rate | `>= 99.0%` successful scheduled runs | Rolling 30 days | Alert if `< 98.0%` | App owner | Two consecutive failures or 30-day breach |
| Max ingestion duration | `p95 <= 15 minutes` | Rolling 14 days | Alert if any run `> 30 minutes` | App owner | Timeout breach or backlog risk |
| Recovery time objective (RTO) | `<= 4 hours` to restore successful daily ingestion after incident | Per incident | Alert if unresolved at `2 hours` | App owner | Incident open beyond warning threshold |

Acceptance checks:
- Targets are observable from runtime metrics/logs.
- Alerts can be simulated in test/staging runbooks.
- Detailed ingestion logs are mandatory and must include at minimum: `run_id`, `stage`, `status`, `started_at_utc`, `ended_at_utc`, `duration_ms`, `source_section`, `source_record_ref`, `error_code`, `error_message`, `exception_type`, `stack_trace`, `retry_count`, and `next_retry_at_utc`.
- Any failed run must expose a human-readable failure summary plus structured machine-readable diagnostics linked to the same `run_id`.

---

## 6) CSV Export Contracts per Endpoint

Goal: lock stable schema per report endpoint.

### 6.1 Endpoint: `GET /reports/pnl/by-instrument`

| Column Name | Type | Nullable | Description | Order |
|---|---|---|---|---|
| `report_date_local` | `date` | No | Report date in `Asia/Jerusalem` | 1 |
| `instrument_id` | `uuid` | No | Internal instrument identifier | 2 |
| `conid` | `text` | No | IBKR canonical instrument identifier | 3 |
| `symbol` | `text` | No | Instrument symbol | 4 |
| `currency` | `text` | No | Reporting currency | 5 |
| `position_qty` | `decimal(24,8)` | No | Open quantity at report boundary | 6 |
| `cost_basis` | `decimal(24,8)` | Yes | Open cost basis | 7 |
| `realized_pnl` | `decimal(24,8)` | No | Realized P&L in report currency | 8 |
| `unrealized_pnl` | `decimal(24,8)` | No | Unrealized P&L in report currency | 9 |
| `total_pnl` | `decimal(24,8)` | No | `realized_pnl + unrealized_pnl` | 10 |
| `provisional` | `boolean` | No | True if affected by unresolved manual case | 11 |

### 6.2 Endpoint: `GET /reports/pnl/by-label`

| Column Name | Type | Nullable | Description | Order |
|---|---|---|---|---|
| `report_date_local` | `date` | No | Report date in `Asia/Jerusalem` | 1 |
| `label_id` | `uuid` | No | Label identifier | 2 |
| `label_name` | `text` | No | Label display name | 3 |
| `instrument_count` | `integer` | No | Count of instruments in label group | 4 |
| `realized_pnl` | `decimal(24,8)` | No | Group realized P&L | 5 |
| `unrealized_pnl` | `decimal(24,8)` | No | Group unrealized P&L | 6 |
| `total_pnl` | `decimal(24,8)` | No | Group total P&L | 7 |
| `fees` | `decimal(24,8)` | No | Group fees total | 8 |
| `withholding_tax` | `decimal(24,8)` | No | Group withholding total | 9 |
| `provisional` | `boolean` | No | True if any contributing row is provisional | 10 |

### 6.3 Endpoint: `GET /reports/reconciliation/diff`

| Column Name | Type | Nullable | Description | Order |
|---|---|---|---|---|
| `report_date_local` | `date` | No | Report date in `Asia/Jerusalem` | 1 |
| `instrument_id` | `uuid` | No | Internal instrument identifier | 2 |
| `conid` | `text` | No | IBKR canonical instrument identifier | 3 |
| `symbol` | `text` | No | Instrument symbol | 4 |
| `metric` | `text` | No | Compared metric name | 5 |
| `broker_value` | `decimal(24,8)` | Yes | Broker-aligned value | 6 |
| `economic_value` | `decimal(24,8)` | Yes | Economic value | 7 |
| `abs_diff` | `decimal(24,8)` | No | Absolute difference | 8 |
| `rel_diff` | `decimal(24,8)` | Yes | Relative difference | 9 |
| `tolerance_abs` | `decimal(24,8)` | No | Applied absolute tolerance | 10 |
| `tolerance_rel` | `decimal(24,8)` | No | Applied relative tolerance | 11 |
| `within_tolerance` | `boolean` | No | Whether diff passes policy | 12 |
| `formula_context` | `text` | Yes | Formula/rule identifier text | 13 |
| `source_event_id` | `uuid` | Yes | Canonical event link | 14 |
| `source_raw_record_id` | `uuid` | Yes | Raw row provenance link | 15 |
| `provisional` | `boolean` | No | True if unresolved manual case affects output | 16 |

Schema versioning:
- Current schema version: `v1`
- Breaking-change policy: increment version and keep fixtures per version.

Acceptance checks:
- Exports are deterministic in column order and naming.
- Fixture regression tests validate schema and sample rows.

---

## 7) Derived Diagnostics Retention and Archival

Goal: define bounded retention for high-volume derived diagnostics while keeping raw data indefinitely.

| Data Class | Keep Duration | Archival Policy | Purge Method | Restore Expectation |
|---|---|---|---|---|
| Ingestion diagnostics | 60 days hot | Archive monthly to compressed JSONL (`.jsonl.gz`) by run date | Purge hot rows older than 60 days after archive checksum success | Restorable to analysis tables within 4 hours |
| Reprocess diagnostics | 60 days hot | Archive monthly to compressed JSONL (`.jsonl.gz`) by run date | Purge hot rows older than 60 days after archive checksum success | Restorable to analysis tables within 4 hours |
| Snapshot diagnostics | 60 days hot | Archive monthly aggregates only (not full row-level payload) | Purge full-row diagnostics older than 60 days | Aggregate-level restore available within 24 hours |

Acceptance checks:
- Retention jobs run without deleting immutable raw payloads.
- Archived diagnostics remain queryable within agreed restore process.

---

## 8) Required Flex Section Matrix

Goal: freeze section-level ingestion requirements so MVP features are deterministic and schema-drift behavior is explicit.

### 8.1 Hard-required sections (fail ingestion run if missing)

- `Trades`
- `OpenPositions`
- `CashTransactions`
- `CorporateActions`
- `ConversionRates`
- `SecuritiesInfo`
- `AccountInformation`

### 8.2 Reconciliation-required sections (fail when reconciliation mode is enabled)

- `MTMPerformanceSummaryInBase`
- `FIFOPerformanceSummaryInBase`

### 8.3 Future-proof ingest-now sections (non-blocking if absent)

- `InterestAccruals`
- `ChangeInDividendAccruals`
- `OpenDividendAccruals`
- `ChangeInNAV`
- `StmtFunds`
- `UnbundledCommissionDetails`

### 8.4 Optional-but-ignored sections in MVP mapping

All other sections are persisted in immutable raw artifacts only and are not mapped into MVP canonical event tables until explicitly promoted by post-MVP scope.

Validation behavior:
- Missing hard-required sections must fail ingestion with deterministic diagnostic code `MISSING_REQUIRED_SECTION` and explicit section names.
- Missing reconciliation-required sections must fail reconciliation publish paths with the same diagnostic convention.

---

## 9) API List Pagination, Sorting, and Filter Contract

Goal: freeze deterministic list endpoint behavior while keeping page-size defaults configurable.

Global list query contract:
- Query params: `limit`, `offset`, `sort_by`, `sort_dir`.
- Configurable defaults: `API_DEFAULT_LIMIT=50`, `API_MAX_LIMIT=200`, `API_DEFAULT_SORT_DIR=asc`.
- Validation:
	- `limit < 1` -> `400 INVALID_PAGINATION`
	- `offset < 0` -> `400 INVALID_PAGINATION`
	- unsupported `sort_by` -> `400 INVALID_SORT_FIELD`
	- unsupported `sort_dir` -> `400 INVALID_SORT_DIRECTION`
	- `limit > API_MAX_LIMIT` -> clamp to `API_MAX_LIMIT` and expose `applied_limit` in response metadata.

Endpoint-specific defaults and allowed fields:

### 9.1 `GET /instruments`
- Default sort: `symbol asc, instrument_id asc`
- Allowed `sort_by`: `symbol`, `conid`, `updated_at_utc`
- Allowed filters: `label_id`, `search`, `active_only`

### 9.2 `GET /notes`
- Default sort: `created_at_utc desc, note_id desc`
- Allowed `sort_by`: `created_at_utc`, `updated_at_utc`, `instrument_id`
- Allowed filters: `instrument_id`, `label_id`, `created_from`, `created_to`

### 9.3 `GET /ingestion/runs`
- Default sort: `started_at_utc desc, ingestion_run_id desc`
- Allowed `sort_by`: `started_at_utc`, `ended_at_utc`, `status`, `duration_ms`
- Allowed filters: `status`, `from_utc`, `to_utc`, `run_type`

List response envelope (all list endpoints):
- `items`: array
- `page`: `{limit, offset, returned, total, has_more, applied_limit}`
- `sort`: `{sort_by, sort_dir}`
- `filters`: normalized applied filters

Contract boundary:
- Runtime-configurable: `API_DEFAULT_LIMIT`, `API_MAX_LIMIT`, `API_DEFAULT_SORT_DIR`.
- Code-level constants: per-endpoint allowed sort/filter fields and default sort keys.

---

## 10) Backup, PITR, and Restore Runbook Policy

Goal: ensure production recovery behavior can satisfy frozen reliability targets (`RTO <= 4h`) with measurable restore confidence.

Backup scope:
- PostgreSQL physical base backup.
- PostgreSQL WAL archive for point-in-time recovery.
- Application restore prerequisites: runtime config and migration metadata.

Cadence and retention:
- Full base backup every `24h`.
- WAL archive shipping every `5m` (archive lag target `<= 5m`).
- PITR window: `14 days`.
- Backup retention:
	- Daily backups: `14 days`
	- Weekly backups: `8 weeks`
	- Monthly backups: `12 months`

Recovery objectives:
- RPO target: `<= 15 minutes`.
- RTO target: `<= 4 hours`.
- Preferred restore path: latest successful base backup + WAL replay to selected recovery timestamp.

Verification and drills:
- After each backup: checksum/integrity verification and backup catalog entry.
- Weekly: non-production restore smoke test from latest successful backup.
- Monthly: full restore drill with measured elapsed recovery time and post-restore validation checklist.
- Failed backup or drill must open an incident with owner, remediation action, and due date.

Post-restore validation minimum:
- Application health endpoint responds successfully.
- Database migration state matches expected schema.
- Latest ingestion runs are queryable.
- Reporting endpoints return successful responses for a known date range.

Operational ownership:
- Runbook owner role is required and must be documented.
- Runbook must include trigger conditions, command sequence, escalation path, and completion sign-off.

---

## Decision Sign-off

| Section | Status (`open`/`approved`) | Decision Owner | Decision Date | Notes |
|---|---|---|---|---|
| Natural keys | approved | Product + Engineering | 2026-02-14 | Reference-informed baseline frozen for MVP |
| Fallback hierarchies | approved | Product + Engineering | 2026-02-14 | Deterministic ordering and tie-breaks frozen |
| Corporate action allowlist | approved | Product + Engineering | 2026-02-14 | Deterministic auto-only policy frozen |
| Tolerance matrix | approved | Product + Engineering | 2026-02-14 | Baseline thresholds frozen |
| SLO targets | approved | Product + Engineering | 2026-02-14 | Operational baseline frozen |
| CSV contracts | approved | Product + Engineering | 2026-02-14 | v1 column contracts frozen |
| Retention windows | approved | Product + Engineering | 2026-02-14 | Hot/archival windows frozen |
| Required Flex section matrix | approved | Product + Engineering | 2026-02-14 | Hard-required, reconciliation-required, and non-blocking future-proof section policy frozen |
| API list contract | approved | Product + Engineering | 2026-02-14 | Configurable pagination defaults and deterministic sort/filter/envelope rules frozen |
| Backup/restore policy | approved | Product + Engineering | 2026-02-14 | Base backup, WAL/PITR, retention, drill cadence, and validation/runbook requirements frozen |
| Single-account API contract (`account_id`) | approved | Product + Engineering | 2026-02-14 | `account_id` internal-only in MVP payloads; fixed configured account context in backend |
