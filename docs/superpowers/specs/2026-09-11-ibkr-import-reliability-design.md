# IBKR import reliability: reevaluation and design

The user requested reevaluation of every review finding, a plan before implementation, and conservative treatment of changed transaction data. This design supersedes automatic live trade correction behavior where stated below. Previously successful history remains replayable; this work does not repair production data or add a correction-approval UI.

## Reevaluation

| Finding | Realistic trigger / evidence | Decision |
| --- | --- | --- |
| Failed semantic writes leak into later imports | Database errors, process termination, or snapshot validation after canonical writes. Synthetic fault injection reproduced it. | Fix. Commit canonical events, lots, snapshots, artifact completion and success together. Keep raw evidence and failure audit independent. |
| Execution ID changes for the same transaction | Report configuration can omit audit fields; no evidence that an existing execution normally changes its identity. | Reject conflicting identities and execution economics with field-level diagnostics. Do not merge aliases, infer corrected quantities, or rewrite identities. Keep existing blank-ID BookTrade fallback for execution rows. |
| Placeholder execution ID | Reproduced with synthetic placeholders; not established as normal IBKR output. | Small malformed-input check only. Reject supplied null-sentinel IDs, rather than interpreting them as shared identities. |
| Multiple broker accounts | IBKR explicitly permits linked accounts in a Flex query. The application supports one account. | Reject mixed accounts and inconsistent row/header accounts. Bind subsequent reports to the prior successfully imported broker account; do not equate an internal account label with a broker ID. |
| Broker base currency differs from USD | Account base currency is configurable; fxRateToBase refers to that currency. | Require USD account metadata on new imports. Do not build general base-currency conversion support. |
| Abandoned started run | Worker/container termination after creating the audit row. | Hold a PostgreSQL session advisory lock for the whole job. After acquiring it, mark any leftover started row interrupted. A live lock holder must never be displaced by elapsed time. No heartbeat subsystem. |
| Downloaded evidence lost on preflight failure | Wrong query selection, removed sections, invalid XML/CSV export. | Persist report bytes and hash before rejecting the report. Retain parsable report date where available. Failed reports stay ineligible for replay. |
| Transient HTTP/network failures abort immediately | Service overload, gateway errors, connect/read interruption. | Bounded backoff for connection errors, timeouts and 429/502/503/504; honor Retry-After without unbounded sleeping. Preserve fatal errors and redaction. |
| Any past failure permanently disables incremental import | A single token/network failure triggers it today. | New atomically processed runs do not poison incremental eligibility. Preserve the conservative guard for legacy failures whose partial writes cannot be ruled out. No automatic legacy data repair. |
| Reference schema/date recommendations | Query settings can change; raw timestamps without zones are already a documented compatibility policy. | Validate the account/base contract and identity requirements needed for safety. Document required execution/audit settings. Do not change historical timezone interpretation or copy reference parser quirks. |

## Sources and limits

- IBKR Flex token/account selection: https://www.interactivebrokers.com/docs/web-api/flex-web-service/client-portal-configuration/enable-and-create-access-token
- IBKR Activity Flex fields and cancellation linkage: https://www.ibkrguides.com/reportingreference/reportguide/financialinformation_fq.htm
- IBKR report-generation endpoint and pacing: https://www.interactivebrokers.com/docs/web-api/api-reference/send-request
- Reference typed metadata: `references/ibflex2/ibflex/Types.py`, `FlexStatement` and `AccountInformation`.
- Reference account filtering: `references/ngv_reports_ibkr/ngv_reports_ibkr/custom_flex_report.py`.

These sources establish available fields and configuration hazards. They do not establish that arbitrary changes to an existing execution are routine or safe to accept.

## Chosen approach

Retain the current synchronous layered architecture. Introduce a DB-owned transaction scope shared by repositories for an engine within the current execution context. This allows the existing mapper and ledger to observe their uncommitted writes without committing intermediate results. Existing explicit ledger connections for manual split previews remain authoritative. A session advisory lock protects the entire run, including acquisition/download and audit finalization; it is separate from the short semantic transaction.

Automatic identity merging was rejected because conflicting source identities need investigation. A new versioned-event publication subsystem was rejected because a shared PostgreSQL transaction solves the current failure window with less code. Heartbeat/lease expiry was rejected because it risks displacing a slow live worker and requires additional operational machinery.

## Input and consistency policy

New imports require exactly one FlexStatement, a broker account ID, AccountInformation with USD base currency, and consistent nonempty account IDs on rows. The broker account must match prior successful raw AccountInformation when such history exists. Preserve immutable downloaded bytes before this validation. Existing historical replay reads stored rows under the historical successful-source contract; it is not a new broker import.

For incoming executions, compare identities within the report and against canonical rows. A single transaction cannot acquire a different execution ID, and a single execution cannot acquire a different nonempty transaction ID. Reject changes to instrument, side, quantity, execution timestamp, currency, price, commission, fees, or net cash. Compare normalized numeric values, not textual formatting. Do not reject changing report-derived close prices, FIFO/tax cost and realized P&L, base FX inputs, or descriptions merely because they refresh; retain their current source/audit behavior. A mismatch is a failed import with conflicting field names and source identity, no canonical mutation, and retained raw evidence. Genuine trade corrections affecting protected execution fields need operator investigation; this change introduces no silent override or repair endpoint.

## Global constraints

- Keep all SQL in `app/db`; never import runtime code from `references/`.
- Preserve raw payloads and rows; no production-data rewrites or destructive cleanup.
- Reject inconsistent execution identities and protected economics; do not merge them automatically.
- Keep historical successful-source replay and manual split approvals working.
- Tests use synthetic reports and temporary PostgreSQL only; no live IBKR calls or external alerts.
- Preserve token redaction and bounded retry budgets.
- Match existing style; no unrelated refactoring or new dependencies.

## Verification

Use real PostgreSQL tests for rollback, successful publication, duplicate retry, account binding, identity conflicts and session-lock ownership/recovery. Inject snapshot/finalization faults and check both stored ledger values and raw audit evidence. HTTP tests use MockTransport and recorded sleeps. Run the full pytest suite, Ruff and MyPy after integration. Any changed historical test must identify whether it is testing the new live contract or deliberately seeding previously accepted history for replay.
