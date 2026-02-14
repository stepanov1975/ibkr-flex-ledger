# MVP Evaluation: IBKR Trade Ledger & Portfolio Insights

Date: 2026-02-14

## 1) Summary
The MVP scope is clear and well-structured across ingestion, canonical mapping, ledger computation, reporting, and reconciliation. The plan strongly enforces modular boundaries and deterministic recomputation from immutable raw data, which is a solid foundation for later expansion.

The largest remaining risk is not feature definition but specification depth in key operational and accounting rules. Several areas need explicit decisions before implementation to avoid rework: idempotency policy, valuation source for unrealized P&L, FX conversion conventions, reconciliation tolerance rules, and authentication/authorization boundaries.

## 2) Missing Information to Fill
- Precise idempotency behavior for repeated ingestion of the same report period (reject, replace, or version).
- Canonical event schema details (required fields per event type, enum values, nullability).
- Instrument identity strategy (IBKR conid vs ISIN/cusip/symbol precedence and conflict handling).
- Pricing source and timing for unrealized P&L (IBKR close, prior close, custom mark).
- FX conversion policy (trade-date rate, settlement-date rate, broker-provided rate precedence).
- Reconciliation diff thresholds (rounding precision, acceptable tolerances, materiality rules).
- Corporate action manual workflow ownership and SLA (who resolves flags, how quickly).
- Data retention policy for raw payloads, logs, and snapshots.
- Security model details (single-user vs multi-user, role boundaries, audit needs).
- Operational SLOs (max ingestion latency, acceptable daily failure rate, recovery time objective).

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
A1: [User answer]
Recommendation: Prefer immutable run history with deduplicated canonical write keys and explicit "superseded_by" linkage to keep auditability.

Q2: Which instrument identifier is canonical when source identifiers conflict (conid, ISIN, symbol)?
A2: [User answer]
Recommendation: Use conid as primary broker key, store ISIN/symbol as attributes, and define deterministic conflict precedence.

Q3: What pricing source and timestamp should be used for unrealized P&L snapshots?
A3: [User answer]
Recommendation: Start with IBKR end-of-day marks tied to report date and document fallback when missing.

Q4: Which FX conversion rule is authoritative for economic reporting (trade-date, settlement-date, or broker rate)?
A4: [User answer]
Recommendation: Use broker-provided execution FX when present; otherwise apply a single documented fallback hierarchy.

Q5: What tolerance and rounding policy defines a reconciliation mismatch?
A5: [User answer]
Recommendation: Define per-metric tolerance (for example, cash vs P&L) and apply currency-specific decimal precision.

Q6: Which corporate action types are mandatory to auto-handle in MVP vs always manual?
A6: [User answer]
Recommendation: Lock a minimum supported set (split, merger cash-in-lieu, symbol change) and hard-flag all others.

Q7: Who owns manual resolution of flagged events, and what is acceptable SLA?
A7: [User answer]
Recommendation: Assign a named operational owner and set a daily cutoff SLA to protect report freshness.

Q8: What authentication/authorization model is required for MVP APIs and UI?
A8: [User answer]
Recommendation: If truly single-user behind reverse proxy, document that boundary; otherwise define RBAC now to avoid schema rework.

Q9: What retention windows apply to raw payloads, diagnostics, and daily snapshots?
A9: [User answer]
Recommendation: Keep raw payloads indefinitely for audit, and add configurable retention for derived diagnostics if storage is constrained.

Q10: What are the target operational SLOs for ingestion success, latency, and recovery?
A10: [User answer]
Recommendation: Define minimum SLOs before implementation so alerts and job behavior can be tuned to objective criteria.

Q11: What should happen if a required Flex section is temporarily missing for one day?
A11: [User answer]
Recommendation: Mark run failed, preserve partial diagnostics, and prevent downstream recompute from publishing incomplete snapshots.

Q12: What level of export capability is mandatory in MVP (CSV/JSON, report-level, provenance-level)?
A12: [User answer]
Recommendation: Start with CSV for key report endpoints and include stable column contracts to support external checks.
