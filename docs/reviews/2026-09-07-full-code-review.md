# Application review — reassessed against existing data

Reassessment date: September 7, 2026. This report supersedes the original ten-item priority list.

Implementation update: the retained code issues have been fixed and validated with 388 passing tests. Maintenance timers, a fresh backup, and an isolated restore drill are complete. Application deployment and the stored expense repair are complete; direct LAN access is preserved. See [fix status and validation](/stock_app/docs/reviews/2026-09-07-real-data-fixes.md). The evidence below records the pre-fix assessment.

**Three P2 defects are confirmed using existing data and current application code.** Four other findings describe reproducible security/operational risks whose triggering conditions were not observed. Three input-dependent findings are excluded from the active list because their assumptions do not match the stored reports.

The original review overstated practical applicability by presenting all reproduced scenarios at similar priority. Synthetic reproducibility does not establish impact on this installation. The dispositions below distinguish those cases explicitly.

## Evidence and scope

The reassessment examined the configured application database using PostgreSQL connections enforcing `default_transaction_read_only=on`. Queries, XML parsing, and application calls emitted aggregates without credentials or account identifiers. No production data was changed. Current-code snapshot recomputation captured persistence calls in memory.

Observed dataset:

- 16 raw report artifacts, each containing exactly one Flex statement; one account across all artifacts.
- 195,312 FX rows, all with their own `reportDate`.
- 32 ingestion/reprocess runs: 27 successful and five failed. All five failures are classified as timeouts.
- 2,109 stored instrument snapshots; latest snapshot date **2026-09-04**, containing **241 instruments**.

The running application image differs from the current working tree in ingestion, mapping, canonical persistence, and reporting files. Findings below were verified using **current workspace code with actual database inputs**. Local router checks used FastAPI TestClient; they do not claim that the older running image produces identical responses.

## Confirmed defects affecting existing data

### [P2] Standalone tax and fee amounts are missing from dedicated expense totals — original #4

Location: [service.py:481](/stock_app/app/mapping/service.py:481), lines 481–482. Consumer: [snapshot_service.py:322](/stock_app/app/ledger/snapshot_service.py:322), lines 322–327.

Actual instrument-associated USD cashflow history through the latest snapshot contains:

| Cashflow type | Events | Net expense |
|---|---:|---:|
| Withholding Tax | 104 | $383.41 |
| Other Fees | 4 | $11.52 |

Their dedicated canonical `withholding_tax` and `fees` fields are null/zero. Across 44 affected latest-snapshot instruments, 42 have nonzero withholding in cash history but report zero withholding tax. All 44 snapshots are nonprovisional. The affected instruments' reported fee total is $283.510303 and excludes the $11.52 of standalone fees.

To rule out stale stored calculations, the current mapper was run on all **108 actual source rows**: dedicated mapped tax/fee totals remained zero. Current snapshot code then recomputed all **241 latest instruments** using actual broker and ledger inputs, with writes captured in memory, reproducing the missing totals.

**Impact is expense breakdown accuracy, not missing expenses from net P&L.** Signed cash amounts already enter realized P&L. The separate costs/dividends views classify cashflow types and should not be described as affected by this omission. Snapshot expense metrics, label expense aggregation, and reconciliation expense metrics are the relevant surfaces.

Fix direction: classify standalone cash expenses into dedicated totals while preserving the existing net cash effect. Simply filling the fields without adjusting the accounting would subtract the expense twice.

### [P2] Closed positions generate false reconciliation failures — original #6

Location: [portfolio.py:952](/stock_app/app/db/portfolio.py:952), lines 952–955; corresponding unrealized-P&L lookup at lines 957–960.

At the latest snapshot date, **137 closed instruments** have zero quantity and zero unrealized P&L and are absent from the present `OpenPositions` section. Their valuation source is `broker_position_absent`; 136 of these stored snapshots are finalized/nonprovisional.

Current reconciliation code produces **274 failed/provisional comparisons** for these instruments: one quantity and one unrealized-P&L comparison each. Every comparison has broker value `null` and economic value `0`. The missing row in a complete section is incorrectly treated as unavailable data.

Both prerequisite reconciliation sections exist. Current API router checks returned HTTP 200 for latest-date and all-history requests, confirming this is a reachable reporting defect with actual inputs.

Fix direction: distinguish an instrument absent from a complete authoritative position section from an unavailable section. Use zero only when completeness establishes that the position is closed.

### [P2] Provenance omits the broker rows actually used for valuation — original #7

Location: [portfolio.py:843](/stock_app/app/db/portfolio.py:843), lines 843–847.

**102 latest snapshots** have corresponding `OpenPositions` rows: 99 use `openpositions_mark_price`, and three use `openpositions_unrealized_pnl`. The current provenance query returns **no OpenPositions source for any of the 102**. One actual instrument has no canonical trade/cashflow/corporate-action history and returns **completely empty provenance** despite having a broker-valued snapshot.

The query includes only canonical event tables, so it omits the actual source of reported position valuation even when trade history is present.

Fix direction: include the authoritative raw valuation row used by the requested snapshot and its artifact lineage.

## Disposition of the remaining original findings

| Original finding | Verified current conditions | Revised disposition |
|---|---|---|
| **#1 Flex token in error diagnostics** | No configured-token matches, token query patterns, or `HTTPStatusError` traces in retained diagnostics/error messages for all 32 runs. Five failures are timeouts. | **Retain as preventive P2 security work, not an observed leak.** An HTTP-status failure is a reasonable operational condition, and the earlier synthetic HTTP 500 reproduction remains valid. Redact the secret-bearing exception chain; do not claim current credentials were exposed. |
| **#2 Multiple accounts merged** | All 16 artifacts contain one statement, and all belong to one account. No missing statement account identifiers. | **Withdraw from the active list for this setup.** Mixed-account rejection is optional defensive validation unless the report/account configuration changes. No account contamination was observed. |
| **#3 Corrected split ratio ignored** | Three canonical corporate actions all require manual handling. None has `ratio` or `newQuantity`/`oldQuantity`. Across 56 raw corporate-action rows and four repeated action/conid keys, no factor signature changed. | **Withdraw from the active-data list.** The synthetic corrected-ratio regression is valid, but the automatic ratio-correction path is not exercised by existing input. Revisit when explicit-factor automatic corporate actions are used. |
| **#5 FX inherited-date comparison** | All 195,312 FX rows carry their own `reportDate`; zero inherited-date cases. | **Withdraw for the current report format.** Date changes are part of the compared payload. The synthetic undated-row case also retained the same numeric rate through prior-date fallback; it did not demonstrate incorrect P&L. |
| **#8 Stuck-run duration alert** | No active runs and no runs over 30 minutes. Maximum recorded duration is 300,352 ms, about five minutes. Outbound alert channels are unconfigured. | **Downgrade to P3 operational resilience.** A stuck run would still be omitted from duration monitoring, but no missed overrun was observed. No need to implement automatic run termination to address the monitoring issue. |
| **#9 Unscoped replay uses startup date** | The current container started September 7, the same day as this check. Retained container logs contain no replay HTTP requests; the UI has no replay trigger. Ten of 11 historical replay runs target a date different from their execution date, consistent with deliberate historical scope; the logs do not establish their trigger surface. | **Downgrade to P3 for an unobserved API usage condition.** The frozen default date is a real code issue, but no wrong-date replay was demonstrated. Explicitly scoped replay avoids it. The absence of requests in short-lived container logs is not proof the endpoint has never been used. |
| **#10 Retained-backup checksum paths** | Five daily archives, zero weekly/monthly copies. Every existing daily archive passes `sha256sum -c`. | **Downgrade to P3 before enabling weekly/monthly copies.** Copied sidecars would retain the daily path, but there is no current retained-copy verification failure or evidence of corrupt backups. |

These scenarios remain documented so future configuration changes do not erase the useful code analysis. They should not compete with the three demonstrated data/reporting defects as if their current impact were established.

## Deployment and operations observations

These are verified installation conditions, separate from the three code defects above:

1. **The loopback-port change has not reached the running container.** The current Compose file binds the application port to loopback; the running app still publishes port 8000 on all IPv4/IPv6 interfaces. The application has no in-app authentication. This leaves a potential proxy bypass if the port is reachable. Reachability beyond the host/firewall was not tested.
2. **Only the ingestion timer is installed/enabled.** The alert, backup, diagnostics-retention, and restore-drill timers are not installed. No alternative external scheduler was verified.
3. **The newest stored base backup and restore-drill evidence are from August 22.** All five existing daily archives pass their SHA-256 checks. WAL archiving is enabled and was current during inspection, with zero failures in the current statistics. Therefore, the old base-backup date must not be described as a proven 16-day data-loss window; point-in-time restore coverage was not tested.
4. **Outbound alerts are unconfigured.** Both webhook and SMTP destinations are absent, and delivery-state storage is empty. The current 30-day SLO calculation reports 13 successful runs out of 18 scheduled runs, or 72.2%, and raises a success-rate warning. That warning is available through monitoring, but this installation has no configured outbound notification channel. This is a configuration observation, not proof of a broken sender.

No deployment, scheduler installation, outbound notification, broker call, production replay, backup creation, or restore was performed.

## Verification record

Original review checks remain historical evidence: 364 repository tests passed, Ruff and MyPy passed, dependency consistency and audit passed, and shell syntax checks passed. They were not rerun for this documentation-only reassessment.

New evidence used for this reassessment:

- [Ingestion, statement-account, FX-date, and diagnostic scan](/tmp/review_ingestion_real_data.py).
- [Actual corporate-action and cash-expense aggregates](/tmp/review_actual_accounting.py).
- [Current mapper and in-memory snapshot recomputation using actual inputs](/tmp/review_actual_accounting_recompute.py).
- [Actual reconciliation/provenance queries and current API router checks](/tmp/review_reporting_actual.py).
- Read-only Docker metadata/source-hash comparisons, systemd timer inspection, backup inventory/checksum validation, and PostgreSQL archiver statistics.

The database probes were independently rerun during reassessment and produced the counts reported above. Application source and production data remain unchanged; only this review document was revised.
