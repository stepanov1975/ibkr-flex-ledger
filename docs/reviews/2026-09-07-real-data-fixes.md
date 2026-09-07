# Real-data review fixes — implementation and validation

## Implemented

- Standalone withholding and fee cashflows contribute to expense breakdowns without being deducted again from net P&L. Signed cash amounts and canonical/raw semantics remain intact; credits reverse expenses.
- Closed-position reconciliation treats absence as zero only for a complete section with successful source lineage. Missing data remains unknown.
- Provenance includes the broker position row actually used by the snapshot, with raw artifact and ingestion lineage.
- HTTP transport exceptions no longer expose secret-bearing upstream exception chains in persistent diagnostics.
- Active-run elapsed time participates in the 30-minute monitoring threshold, without altering completion statistics or automatically finalizing runs.
- Unscoped HTTP replay resolves the current business date on each request; explicit scopes remain unchanged.
- New backup checksum sidecars use portable archive names. WAL verification supports both portable and existing absolute sidecars.

Withdrawn findings about mixed accounts, corrected split factors, and undated FX input were not implemented. Outbound alerts remain dashboard-only, as requested.

## Validation

- Final full PostgreSQL-backed suite: **388 passed**.
- Ruff and MyPy passed; MyPy checked 65 source files. Dependency consistency, shell syntax, and diff whitespace checks passed.
- Independent review approved the reporting, security, monitoring, replay, and backup changes.
- Docker image built successfully and its relevant source hashes match the final reviewed files.
- Actual-data read-only recomputation covered **2,109 snapshots** across nine date/run scopes. Comparing pre-fix workspace code with the new code, all quantities, cost basis, realized/unrealized/total P&L, and provisional values are identical.
- Latest computed expense breakdowns recover **$383.41 withholding tax** and **$11.52 standalone fees**.
- Current reporting code restores **102 valuation provenance rows** and eliminates the **274 false unknown comparisons** for closed positions.

## Operations completed

- Created and verified `/backups/base/20260907T174225Z.tar.gz` using the updated backup script.
- Completed an isolated restore drill in **6 seconds**. Evidence: `var/restore-drills/20260907T174357Z.json`.
- Installed/enabled ingestion, daily backup, diagnostics-retention, and weekly restore-drill timers. The outbound-alert timer is disabled.
- Updated scheduler documentation for dashboard-only alerts and backup documentation for portable checksum verification.

## Deployment and stored-data repair pending

The reviewed application image is ready, but the running application has not been replaced. Its port remains bound to all interfaces. The user was asked whether access is direct, local, or through a proxy/tunnel; that answer is still pending. No reverse proxy was found on this host. Applying the loopback-only Compose configuration before resolving access could disconnect the user.

Stored snapshot expense totals have not yet been changed. An expense-only repair is prepared at `/tmp/real-fixes-expense-repair.py`; its read-only preview identifies **360 rows**. It adds only this task’s before/after calculation deltas to stored `fees` and `withholding_tax`, preserving prior expense adjustments and existing stored quantities, cost basis, P&L, and provisional status. It checks the saved snapshot baseline, takes the same account advisory lock as ingestion plus a snapshot table lock, and aborts if a run is active or snapshot values changed.

The exact apply path passed against a disposable PostgreSQL database containing all 2,109 original snapshot rows: 360 rows changed on the first application, zero on repeat, and a changed non-expense baseline correctly aborted the repair. The disposable database was removed afterward. Independent operational review approved the corrected repair, confirming that it applies only this task’s expense deltas.

This narrower repair replaces the initial full-replay idea: the working tree already contained earlier fixes whose calculations differ from historical persisted P&L. Applying a full replay would mix those pre-existing changes into this repair. After resolving access and deploying, refresh read-only calculations if the dataset changed, apply the reviewed expense-only repair, and verify actual API/storage results and unchanged non-expense fields.

The user requested committing and pushing all workspace changes on `codex/review-fixes`, including changes that predated this implementation. Deployment and stored-data repair remain pending as described above. The starting working-tree copy is at `/tmp/stock-real-fixes-baseline`.
