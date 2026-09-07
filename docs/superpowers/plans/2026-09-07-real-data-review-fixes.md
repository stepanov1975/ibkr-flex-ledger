# Real-data review fixes implementation plan

Approved scope: user requested fixes to the reassessed review report. Retain the single-account USD architecture and existing uncommitted work. Fix observed report defects (#4/#6/#7), retained security issue (#1), and small operational code issues (#8/#9/#10). Exclude withdrawn input-format scenarios (#2/#3/#5). Outbound alerts remain dashboard-only per user instruction.

## Design and constraints

- Preserve signed canonical cash amounts, raw records, and net P&L. Separate expense classification from net-cash deductions, including credits/reversals and explicit supplemental fields. No schema migration unless evidence proves necessary.
- Closed positions become zero only when an authoritative complete OpenPositions section establishes absence; genuinely missing data remains unknown.
- Provenance must use the actual requested snapshot's raw position row, not a newer unrelated artifact.
- Suppress/redact secret-bearing transport exception causes before persistent diagnostics; retain useful sanitized status/type details.
- Measure active-run elapsed time at evaluation; do not auto-finalize runs.
- Resolve implicit HTTP replay date at execution; preserve explicit scoped replays.
- Backup checksum sidecars must remain valid after directory copy and daily expiration; adapt WAL verification to portable sidecars and retain compatibility with existing absolute paths.
- Work in existing codex/review-fixes checkout; task extends user-owned uncommitted fixes. Baseline saved at /tmp/stock-real-fixes-baseline. Do not reset/stash/commit unrelated changes.

## Tasks and verification

1. Reporting/accounting: add failing regression tests from actual patterns (100 dividend, -15 tax, -2 fee => net83/tax15/fees2), tax reversal, supplemented cash expenses, FX amounts, closed/absent versus missing section, actual position provenance incl. broker-only and replay lineage. Implement minimal changes to ledger record/read/service and portfolio query as needed; keep canonical raw semantics. Verify focused tests and read-only actual-data probes/in-memory recomputation.
2. Security and operations: add failing tests for HTTP500 synthetic token absent from full stored trace, active run at/beyond30min, API crossing midnight without explicit replay scope, copied backup checksums surviving original deletion. Fix corresponding adapter/monitor/bootstrap/backup/WAL code. Verify focused tests.
3. Integration: run full PostgreSQL-backed suite, Ruff, MyPy, shell syntax, dependency consistency. Compare actual-data recomputation before/after: preserve P&L, recover $383.41 tax and $11.52 fees, remove 274 closed-position unknown comparisons, restore 102 valuation provenance rows. Obtain independent review of task diff against saved baseline; fix actionable regressions.
4. Operations: after passing source validation, create/verify database backup, build and apply app changes with the user's access method considered. Preserve raw/canonical identity and apply only this task’s expense deltas to stored snapshots if deploying. Install supported maintenance timers; alerts stay dashboard-only. Refresh backup and run isolated restore drill. Verify running health, port bindings, timer configuration, and repaired stored metrics. If access configuration is missing, finish all independent work and report the precise remaining requirement.

## Progress

- Initial baseline: prior suite 364 passed; full working-tree baseline preserved.
- User choice: alerts remain dashboard-only; direct LAN access confirmed and preserved.

- Tasks1–3 complete: reporting/security/operational code changes implemented;388tests passed; Ruff/MyPy passed; independent review approved; final image built and source hashes verified.
- Operations complete: fresh verified backup, isolated6secondrestore drill, maintenance timers installed; alert timer disabled.
- Ruling: replace full historical replay with expense-only snapshot repair. Existing stored non-expense values differ from pre-existing workspace calculations; replay would apply unrelated earlier fixes. Prepared guarded repair preview 360 rows; disposable-database apply/idempotence/stale-baseline checks passed; the guarded live repair subsequently updated those 360 rows.
- Complete: restored original LAN-compatible port publication, deployed the reviewed image, applied the guarded expense-only repair, and verified live LAN APIs and all 2,109 snapshots. Non-expense values and raw/canonical row counts remain unchanged.
