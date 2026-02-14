# MVP Evaluation

Date: 2026-02-14
Scope evaluated: `MVP.md` (cross-checked against `README.md`, `ai_memory.md`, `MVP_spec_freeze.md`, and `references/REFERENCE_NOTES.md`)

## Evaluation Summary

`MVP.md` is strong on architecture and boundaries, and most critical policy gaps are already resolved. The remaining work is primarily consistency cleanup plus a small set of product decisions needed to prevent implementation churn.

## Issues Found

No blocking policy ambiguities remain in MVP planning documents.

Resolved since initial evaluation (now frozen in specs):
- Corporate-action `SD` rule aligned to auto-when-deterministic across planning docs.
- `pnl_snapshot_daily` is now mandatory in MVP.
- Required Flex section matrix is now explicit and versioned.
- Ingestion overlap policy is now explicit (`409` on concurrent run).
- API list pagination/sorting/filter contracts are now explicit with configurable defaults.
- Backup/restore policy is now explicit (base backup cadence, WAL/PITR, retention tiers, drill cadence, and runbook ownership).
- Single-account `account_id` API contract is now explicit (`account_id` internal-only; fixed configured account context in backend).

## Gaps Filled Without User Input (From References + Project Rules)

1. Parser and accounting boundary should remain isolated (`ibflex` pattern + project modularity rule).
2. Ingestion flow should be deterministic `request -> poll -> download -> persist` with explicit run stages (`ibflex`/`flexquery` pattern).
3. Missing mandatory report sections should hard-fail publishing with structured diagnostics (`finx-reports-ib` checklist pattern).
4. CSV exports should keep fixed column order/versioned schema contracts (`flexquery` CSV contract pattern).
5. DB access must remain repository/db-layer only (project mandatory architecture rule).

## Implementation Challenges

1. Deterministic replay with stable natural keys across parser/version changes.
2. Preventing silent drift when Flex section shapes change but payload remains syntactically valid.
3. Keeping reconciliation tolerance logic perfectly aligned across backend, API, and UI.
4. Maintaining end-to-end provenance (`report row -> canonical event -> raw record`) for every metric.
5. Handling manual corporate-action backlog without blocking unrelated instrument reporting.

## Resolved Decisions (Q1-Q9)

- Q1: `SD` policy is `auto-when-deterministic`.
- Q2: `pnl_snapshot_daily` is mandatory in MVP.
- Q3: Base reporting currency is fixed to `USD` for MVP.
- Q4: Ingestion overlap uses single active run lock with `409` on concurrent trigger.
- Q5: Required Flex section matrix is frozen (hard-required, reconciliation-required, future-proof ingest-now, optional raw-only).
- Q6: List API pagination/sorting/filter contract is frozen with configurable default limits and deterministic allowlists.
- Q7: Backup/restore policy is frozen (24h base backup, 5m WAL archive, 14-day PITR, retention tiers, weekly/monthly restore drills).
- Q8: Unresolved manual corporate-action cases keep only affected instruments provisional and visible; unrelated instruments and global reporting continue.
- Q9: `account_id` is internal-only in MVP API payloads; backend repositories use a fixed configured account context; external multi-account API exposure is deferred to post-MVP.

## Readiness

Current readiness: High for implementation start, Medium-High for production hardening.

- Implementation can start immediately on ingestion/mapping/ledger foundations.
- Remaining completion risk is concentrated in operational runbook execution discipline and implementation quality controls.
