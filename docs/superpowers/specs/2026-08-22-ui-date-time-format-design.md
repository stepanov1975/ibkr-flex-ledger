# UI Date and Time Format Design

## Scope

Standardize every date and timestamp currently displayed by the browser dashboard.
This is a presentation-only change; API payloads and persisted values retain their
existing ISO/UTC contracts.

## Display contract

- Date-only values use `dd/mm/yy` with zero-padded day and month.
- Timestamp values use `dd/mm/yy hh:mm` with zero-padded 24-hour time.
- UTC timestamps are converted to the existing UI timezone, `Asia/Jerusalem`.
- Business date values such as `report_date_local` are reformatted without timezone
  conversion so their calendar date cannot shift.
- Missing or invalid values display an em dash.

## Implementation

Keep the dependency-free dashboard architecture. Add two small JavaScript helpers to
the existing inline dashboard script: one for date-only values and one for timestamps.
Use them for P&L report dates, corporate-action creation timestamps, and ingestion-run
start timestamps.

Document the contract in the normative MVP freeze sheet and the README dashboard
documentation. Add focused dashboard-route regression coverage for the options and
each formatter call site.

