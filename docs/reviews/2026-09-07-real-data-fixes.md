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

- Created and verified `/backups/base/20260907T174225Z.tar.gz` using the updated backup script. Took another verified backup immediately before deployment: `/backups/base/20260907T180519Z.tar.gz`.
- Completed an isolated restore drill in **6 seconds**. Evidence: `var/restore-drills/20260907T174357Z.json`.
- Installed/enabled ingestion, daily backup, diagnostics-retention, and weekly restore-drill timers. The outbound-alert timer is disabled.
- Updated scheduler documentation for dashboard-only alerts and backup documentation for portable checksum verification.

## Deployment and stored-data repair completed

The user confirmed direct LAN access at `http://192.168.1.242:8000/ui/operations`. The earlier loopback-only restriction assumed a proxy that this deployment does not use, so the original LAN-compatible port publication was restored. The application was recreated with the reviewed image (`sha256:5fc08b0fb2a3757b3dac26b30ec57b3927ee2fde71b65916c8e2f22ef11ebc81`) and became healthy. LAN health, operations, portfolio, costs, and SLO endpoints all returned successful responses.

Fresh read-only recomputations matched the reviewed before/after inputs across all 2,109 snapshots. After the verified pre-deployment backup, the guarded expense-only repair at `/tmp/real-fixes-expense-repair.py` updated **360 rows**. A subsequent preview identified zero remaining changes. It added only this task’s calculation deltas to stored `fees` and `withholding_tax`, preserving prior expense adjustments and stored quantities, cost basis, P&L, and provisional status. The transaction checked the saved baseline, took the same account advisory lock as ingestion plus a snapshot table lock, and verified there was no active run.

The repair had first passed against a disposable PostgreSQL database containing all 2,109 original snapshot rows: 360 rows changed on the first application, zero on repeat, and a changed non-expense baseline correctly aborted. Independent operational review approved it.

Post-deployment verification confirmed:

- All **2,109** stored snapshots retain their original non-expense values.
- Latest stored expense breakdowns increased by **$383.41 withholding tax** and **$11.52 fees**.
- The live LAN API returns OpenPositions lineage for all **102** affected provenance reports.
- All **274** targeted closed-position comparisons have zero broker values and pass tolerance checks.
- Raw/canonical, position-lot, and ingestion-run row counts remain unchanged.

This narrow repair replaces the initial full-replay idea: the working tree already contained earlier fixes whose calculations differ from historical persisted P&L. Full replay would mix those pre-existing changes into this repair.

All workspace changes were committed and pushed at the user’s request on `codex/review-fixes`; the LAN-access correction and deployment record follow in the same branch. The starting working-tree copy is at `/tmp/stock-real-fixes-baseline`.
