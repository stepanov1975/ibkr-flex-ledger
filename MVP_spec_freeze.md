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
- Time policy: persist timestamps in UTC; apply `Asia/Jerusalem` for UI and business date boundaries.
- Authentication hardening for proxy headers/trust assumptions: out of MVP scope (post-MVP).

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
| 3 | Last execution `tradePrice` on or before target `report_date` | Trade exists for `conid` with non-null `tradePrice` | Latest `dateTime`; then highest numeric `transactionID` | Use value but mark valuation as provisional with diagnostic code `EOD_MARK_FALLBACK_LAST_TRADE` |

### 2.2 Execution FX Fallback

| Priority | Source | Eligibility Condition | Tie-Break Rule | Missing-Data Behavior |
|---|---|---|---|---|
| 1 | `Trades.fxRateToBase` | Event row has non-null `fxRateToBase` and non-zero denominator context | Highest source priority always wins | If unavailable, continue to priority 2 |
| 2 | Derived from `netCashInBase / netCash` | Both values are present and `netCash != 0` | Round to 10 decimal places using half-even | If unavailable, continue to priority 3 |
| 3 | `ConversionRates` for (`currency`, base, `report_date`) | Matching pair exists for report date (or nearest previous available date) | Pick exact date first, otherwise nearest previous date | If unavailable, use `1.0` only when `currency == base`; otherwise set event provisional and block economic FX output |

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
| Ingestion diagnostics | 180 days hot | Archive monthly to compressed JSONL (`.jsonl.gz`) by run date | Purge hot rows older than 180 days after archive checksum success | Restorable to analysis tables within 4 hours |
| Reprocess diagnostics | 365 days hot | Archive monthly to compressed JSONL (`.jsonl.gz`) by run date | Purge hot rows older than 365 days after archive checksum success | Restorable to analysis tables within 4 hours |
| Snapshot diagnostics | 90 days hot | Archive monthly aggregates only (not full row-level payload) | Purge full-row diagnostics older than 90 days | Aggregate-level restore available within 24 hours |

Acceptance checks:
- Retention jobs run without deleting immutable raw payloads.
- Archived diagnostics remain queryable within agreed restore process.

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
