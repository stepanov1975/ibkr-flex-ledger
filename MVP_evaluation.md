# MVP Evaluation

Date: 2026-02-14
Source: `MVP.md`

## Summary

The MVP is well-scoped and modular, with clear layer boundaries, a reproducibility-first data model, and explicit non-goals. It is strong on architecture intent and auditability requirements (raw immutability, provenance, reprocess determinism, reconciliation visibility). The canonical time policy is now defined: store timestamps in UTC and apply `Asia/Jerusalem` for UI and business date boundaries. The main execution risk is not missing features, but missing operational and data-contract specifics needed to implement consistently (scheduler semantics, strict contract definitions, manual-case workflow details, and acceptance thresholds).

## Missing Information to Fill Before/During Implementation

### Resolved

1. **Flex query and account scope**
	- Resolved: MVP is strictly single IBKR account.
2. **Time boundary conventions**
	- Resolved: store timestamps in UTC; apply `Asia/Jerusalem` for business date boundaries and UI display.
3. **Canonical event identity specification**
	- Resolved decision: define event-type key contracts in one versioned mapping spec before coding mappers.
4. **Mark/FX fallback definitions**
	- Resolved decision: freeze one ordered source list and tie-break behavior for deterministic recompute.
5. **Corporate action manual workflow schema**
	- Resolved decision: maintain explicit auto-allowlist; all other cases are mandatory manual with provisional output flags.
6. **Reconciliation tolerance matrix**
	- Resolved decision: publish a shared tolerance matrix (absolute + relative thresholds) used by UI and tests.
7. **Operational SLO and alert thresholds**
	- Resolved decision: define minimum measurable SLOs before release validation.
8. **CSV export contract**
	- Resolved decision: version CSV schemas and lock column names/order/types with fixture-based regression tests.
9. **Data retention implementation detail**
	- Resolved decision: keep raw data indefinitely; define bounded retention/archival for high-volume derived diagnostics.
10. **Security boundary details**
	- Resolved for MVP scope: authentication header and trust-assumption checks are out of scope and deferred to post-MVP hardening.

### Still Open (needs concrete values/specs)

1. **Canonical event natural keys by type**
	- Exact key field sets for `trade_fill`, `cashflow`, `fx`, and `corp_action` still need explicit specification.
2. **Fallback hierarchies as concrete ordered lists**
	- EOD mark and FX fallback sources require exact priority definitions and deterministic tie-break rules.
3. **Corporate action allowlist detail**
	- Exact action types included in auto-resolution vs mandatory manual are not yet enumerated.
4. **Tolerance matrix values**
	- Absolute/relative thresholds and currency precision need explicit numeric values.
5. **SLO target values**
	- Ingestion success threshold, max run duration, and recovery-time targets remain to be quantified.
6. **CSV contracts per endpoint**
	- Concrete per-endpoint column schemas and version labels still need to be finalized.
7. **Derived-data retention windows**
	- Exact retention durations and archival trigger criteria are not yet specified.

## Assumptions to Clarify and Freeze

- MVP is **single-user, single deployment**, with coordinated code+DB rollout and no backward-compatibility paths.
- Database is PostgreSQL 17 and all write/read access is through `db` layer repositories only.
- Time handling is fixed: persist timestamps in UTC; compute ingestion/report/snapshot boundaries in `Asia/Jerusalem`, then convert to UTC for storage/query.
- Corporate actions are conservative by design: ambiguous cases must block affected outputs as provisional.
- Reprocessing from immutable raw data is source-of-truth for correction and reproducibility.
- Reconciliation mode and economic mode are intentionally different and both valid if policy-defined tolerance is met.

## Implementation Challenges (with Impact)

1. **Deterministic idempotency across ingestion and mapping**
	- Challenge: avoiding duplicate business events while allowing safe replay.
	- Impact: incorrect positions/P&L if keys are unstable.
2. **Instrument identity conflicts (conid-first with aliases)**
	- Challenge: symbol/cusip/isin changes and stale aliases over time.
	- Impact: fragmented instrument history and broken drilldowns.
3. **FIFO lot correctness under partial closes and fees/taxes**
	- Challenge: exact cost basis propagation under complex sequences.
	- Impact: silent realized/unrealized P&L errors.
4. **Schema drift detection quality**
	- Challenge: fail-fast without excessive false positives.
	- Impact: ingestion instability or unnoticed data corruption.
5. **Provenance query performance**
	- Challenge: deep traceability (`report -> event -> raw`) at report latency targets.
	- Impact: slow UX and operational troubleshooting friction.
6. **Manual-case gating UX and downstream consistency**
	- Challenge: marking provisional outputs while preserving user trust.
	- Impact: users may misread incomplete numbers as final.
7. **Reconciliation explainability**
	- Challenge: surfacing formula/rule context for each diff in a compact way.
	- Impact: unresolved mismatches and low confidence in system outputs.

## Open Questions

Q1: Is MVP strictly single IBKR account, or must ingestion/reporting support multiple accounts now?
A1: MVP is strictly single IBKR account.
Recommendation: If uncertain, enforce single-account in MVP schema/API and add account as a future module boundary.

Q2: What canonical timezone should be used for ingestion date boundaries, snapshots, and report filters?
A2: Store all timestamps in UTC in the database. Use Israel local timezone (`Asia/Jerusalem`) for UI display and for business date boundaries (ingestion windows, daily snapshots, and report filters).
Recommendation: Standardize boundary handling as local-time-first (`Asia/Jerusalem`) and convert boundary timestamps to UTC for database querying; enforce timezone-aware datetimes only.

Q3: What are the exact natural-key fields for UPSERT per canonical event type (`trade_fill`, `cashflow`, `fx`, `corp_action`)?
A3: Define event-type key contracts in one versioned mapping spec before coding mappers.
Recommendation: Define event-type key contracts in one versioned mapping spec before coding mappers.

Q4: What is the exact fallback hierarchy for missing EOD marks and missing execution FX?
A4: Freeze one ordered source list and document tie-break behavior to preserve deterministic recompute.
Recommendation: Freeze one ordered source list and document tie-break behavior to preserve deterministic recompute.

Q5: Which corporate action types are auto-resolved in MVP vs always manual?
A5: Maintain an explicit allowlist for auto cases and treat all others as mandatory manual with provisional output flags.
Recommendation: Maintain an explicit allowlist for auto cases and treat all others as mandatory manual with provisional output flags.

Q6: What reconciliation tolerances should be used per metric and currency precision?
A6: Publish a tolerance matrix (absolute + relative thresholds) and use the same matrix in UI and test fixtures.
Recommendation: Publish a tolerance matrix (absolute + relative thresholds) and use the same matrix in UI and test fixtures.

Q7: What SLO targets define MVP reliability (ingestion success %, max run duration, recovery time)?
A7: Set minimum measurable SLOs now so operations and alerting can be validated before release.
Recommendation: Set minimum measurable SLOs now so operations and alerting can be validated before release.

Q8: What is the stable CSV export contract for each report endpoint?
A8: Version CSV schemas and lock column names/order/types with fixture-based regression tests.
Recommendation: Version CSV schemas and lock column names/order/types with fixture-based regression tests.

Q9: What retention/archival policy applies to derived diagnostics and snapshots as data volume grows?
A9: Keep raw indefinitely as required, but define bounded retention and archival for high-volume derived logs.
Recommendation: Keep raw indefinitely as required, but define bounded retention and archival for high-volume derived logs.

Q10: What reverse-proxy authentication headers and trust assumptions are mandatory in production and local development?
A10: No need to check authentication headers and trust assumptions.
Recommendation: Treat this as out of scope for MVP and revisit in post-MVP hardening.

