# Task 3 report: Validate new report account and base currency

## Requirements checklist

- [x] Added the pure
  `job_validate_report_context(payload_bytes, expected_broker_account_ids)` API.
- [x] Requires exactly one `FlexStatement` and exactly one
  `AccountInformation` element.
- [x] Requires `AccountInformation.currency` to resolve to `USD`.
- [x] Resolves the broker account ID from the statement header or
  `AccountInformation`; a row-only ID is insufficient.
- [x] Rejects blank or conflicting `accountId` attributes anywhere in the
  statement, including data rows.
- [x] Rejects linked-account reports and broker account changes relative to
  prior successful broker account IDs.
- [x] Raises `ReportContextError(ValueError)` with stable code
  `REPORT_CONTEXT_INVALID` for all validation failures.
- [x] Uses the existing Flex XML parser and imports no reference-project runtime
  code.

## TDD and verification

- Red: `tests/test_report_context.py` initially failed collection because
  `app.jobs.report_context` did not exist. With the public API stub present, the
  four acceptance cases failed with `ReportContextError`.
- Green: `.venv/bin/pytest -q tests/test_report_context.py` passed all 16 cases.
- Ruff passed for `app/jobs/report_context.py` and
  `tests/test_report_context.py`.
- MyPy with `--follow-imports=skip` passed for both owned source files. The
  normal targeted invocation currently reaches a root-owned orchestrator edit
  and reports its `ValueError.code` narrowing issue at line 515.

## Integration concerns

- The persisted AccountInformation history lookup and ingestion-orchestrator
  hook are owned by the root task. Live ingestion should pass only broker IDs
  from successful raw sources and map `ReportContextError.code` directly to the
  failed-run error code.
- Validation should run after immutable raw persistence and before canonical
  writes. Historical replay should remain under its existing successful-source
  contract.
- The validator deliberately treats `AccountInformation.currency` as the base
  currency because that is the field defined by the repository's IBKR Flex
  field catalog.

## Commit

- Subject: `feat: validate broker report context`
- The resulting commit hash is returned to the root task after commit.
