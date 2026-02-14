# MVP Evaluation: IBKR Trade Ledger & Portfolio Insights

Date: 2026-02-14

## 1) Summary
The MVP scope is clear and well-structured across ingestion, canonical mapping, ledger computation, reporting, and reconciliation. The plan strongly enforces modular boundaries and deterministic recomputation from immutable raw data, which is a solid foundation for later expansion.

The largest remaining risk is not feature definition but specification depth in key operational and accounting rules. Several areas need explicit decisions before implementation to avoid rework: idempotency policy, valuation source for unrealized P&L, FX conversion conventions, reconciliation tolerance rules, and authentication/authorization boundaries.

## 2) Missing Information to Fill
- Idempotency behavior for repeated ingestion is now resolved: raw artifact dedupe by `period_key + flex_query_id + sha256`, then canonical record UPSERT by stable natural keys.
- Canonical event schema details (required fields per event type, enum values, nullability).
- Instrument identity strategy is now resolved: conid is canonical; ISIN/CUSIP/FIGI and symbol/localSymbol are aliases with deterministic conid-first conflict handling.
- Pricing source and timing for unrealized P&L is now resolved: IBKR end-of-day marks tied to report date with documented fallback when missing.
- FX conversion policy is now resolved: use broker-provided execution FX when present; otherwise apply one documented fallback hierarchy.
- Reconciliation diff thresholds are now resolved: per-metric tolerance with currency-specific decimal precision.
- Corporate action handling ownership and workflow are now resolved: conservative automation boundary plus single-user manual case resolution with provisional-output warnings.
- Data retention policy is now resolved: keep raw payloads indefinitely for audit and use configurable retention for derived diagnostics.
- Security model is now resolved: authentication is delegated to reverse proxy; no in-app authentication in MVP.
- Operational SLO baseline is now resolved: define minimum ingestion success, latency, and recovery SLOs before implementation.

## 3) Clarified Assumptions (Current Plan Implies)
- Single deployment track with synchronized app and DB schema changes (no backward-compatibility layer).
- Stocks are the only supported asset class in MVP ledger logic.
- FIFO is authoritative for lot matching and realized P&L.
- Raw Flex data is immutable and acts as the source of truth for replay.
- Corporate actions beyond covered deterministic mapping can be flagged for manual handling.
- API/UI consume data via service and db-layer interfaces only; no direct DB access outside db modules.

## 4) Implementation Challenges and Considerations
1. Event normalization stability
   - Challenge: Flex schema drift can break mappers silently if contracts are loose.
   - Recommendation: Define strict per-section parser contracts and golden fixture tests before coding ledger logic.

2. Deterministic recomputation at scale
   - Challenge: Reprocessing full history can become expensive and hard to reason about with partial failures.
   - Recommendation: Add run-scoped checkpoints and deterministic hash/fingerprint of canonical outputs per run.

3. Accounting consistency between modes
   - Challenge: Broker-aligned and economic modes may diverge for legitimate reasons, creating support noise.
   - Recommendation: Publish a mode rulebook with formula-level differences and expose formula metadata in diff output.

4. Traceability performance
   - Challenge: Row-level provenance joins can slow reporting and drilldowns.
   - Recommendation: Precompute lineage keys and add indexes for event->raw and report->event traversal paths.

5. Corporate action ambiguity
   - Challenge: Edge-case actions can invalidate lot states if partially mapped.
   - Recommendation: Use explicit blocking rules: unresolved mandatory action types halt affected instrument recompute.

6. Operational reliability
   - Challenge: Cron-based ingestion may overlap or run with stale credentials/network issues.
   - Recommendation: Enforce run locks, timeout policy, retry/backoff for transport, and actionable alert summaries.

## 5) Open Questions
Q1: What is the exact idempotency policy for ingesting the same IBKR report period multiple times?
A1: Use upsert-by-stable-record-IDs as the authoritative idempotency rule; treat Flex ReferenceCode only as a temporary retrieval handle and never as a stable report identity. For each ingestion, dedupe raw artifacts by period_key + flex_query_id + sha256; then parse and UPSERT canonical records on stable natural keys so re-ingestion converges to latest IBKR truth without duplicates.
Recommendation: This is the strongest v1 policy because it is deterministic, audit-friendly, resilient to late IBKR corrections, and simple to operate. Add period-window reconciliation (delete/deactivate missing rows) only if strict latest-statement snapshot matching is later required.

Q2: Which instrument identifier is canonical when source identifiers conflict (conid, ISIN, symbol)?
A2: Use conid as the canonical instrument identifier for IBKR-sourced data. Treat ISIN/CUSIP/FIGI and symbol/localSymbol as secondary aliases or attributes, not primary identity.
Recommendation: Conid should be the authoritative key because it is the most stable IBKR contract identifier across sections and edge cases. Store aliases with history (valid_from/valid_to), resolve conflicts by trusting conid first, and use deterministic fallback keys only when conid is missing (with manual-review flag).

Q3: What pricing source and timestamp should be used for unrealized P&L snapshots?
A3: Start with IBKR end-of-day marks tied to report date and document fallback when missing.
Recommendation: Start with IBKR end-of-day marks tied to report date and document fallback when missing.

Q4: Which FX conversion rule is authoritative for economic reporting (trade-date, settlement-date, or broker rate)?
A4: Use broker-provided execution FX when present; otherwise apply a single documented fallback hierarchy.
Recommendation: Use broker-provided execution FX when present; otherwise apply a single documented fallback hierarchy.

Q5: What tolerance and rounding policy defines a reconciliation mismatch?
A5: Define per-metric tolerance (for example, cash vs P&L) and apply currency-specific decimal precision.
Recommendation: Define per-metric tolerance (for example, cash vs P&L) and apply currency-specific decimal precision.

Q6: Which corporate action types are mandatory to auto-handle in MVP vs always manual?
A6: Auto-handle only deterministic, low-ambiguity actions in MVP: cash-only events (CD, CP, FA), identity-only changes (IC and security ID changes), and FS/RS only when split-factor inference is unambiguous. Treat all election-based, multi-leg, cost-basis allocation, and option-deliverable adjustment actions as manual cases.
Recommendation: Use a three-tier policy: Safe Auto, Auto-if-unambiguous, and Always Manual. Always ingest and classify all corporate action rows, then open a manual case whenever inference is not deterministic.

Q7: Who owns manual resolution of flagged events, and what is acceptable SLA?
A7: The single application user owns manual resolution of flagged events. There is no fixed time-based SLA; resolution is performed when the user has availability.
Recommendation: Mark affected outputs as provisional until unresolved flags are cleared, with persistent warnings and unresolved counters so data quality state is always visible.

Q8: What authentication/authorization model is required for MVP APIs and UI?
A8: Authentication is handled by the reverse proxy. The application itself will not implement authentication.
Recommendation: If truly single-user behind reverse proxy, document that boundary; otherwise define RBAC now to avoid schema rework.

Q9: What retention windows apply to raw payloads, diagnostics, and daily snapshots?
A9: Keep raw payloads indefinitely for audit, and add configurable retention for derived diagnostics if storage is constrained.
Recommendation: Keep raw payloads indefinitely for audit, and add configurable retention for derived diagnostics if storage is constrained.

Q10: What are the target operational SLOs for ingestion success, latency, and recovery?
A10: Define minimum SLOs before implementation so alerts and job behavior can be tuned to objective criteria.
Recommendation: Define minimum SLOs before implementation so alerts and job behavior can be tuned to objective criteria.

Q11: What should happen if a required Flex section is temporarily missing for one day?
A11: Mark run failed, preserve partial diagnostics, and prevent downstream recompute from publishing incomplete snapshots.
Recommendation: Mark run failed, preserve partial diagnostics, and prevent downstream recompute from publishing incomplete snapshots.

Q12: What level of export capability is mandatory in MVP (CSV/JSON, report-level, provenance-level)?
A12: Start with CSV for key report endpoints and include stable column contracts to support external checks.
Recommendation: Start with CSV for key report endpoints and include stable column contracts to support external checks.
