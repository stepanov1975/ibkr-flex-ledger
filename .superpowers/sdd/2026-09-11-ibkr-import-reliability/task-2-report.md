# Task 2 report: Conservative execution consistency

## Result

- Added `app/db/trade_consistency.py` with the requested
  `db_validate_trade_consistency(connection, requests)` API and
  `TradeConsistencyError(ValueError)`.
- Added one batched PostgreSQL lookup for all incoming execution and transaction
  identities. Incoming-batch conflicts are checked before the database lookup.
- Rejected transaction-to-execution changes, changes between nonempty
  transaction IDs for one execution, and changes to protected execution fields.
- Rejected explicit Flex `ibExecID` null sentinels (`-`, `--`, `N/A`) while
  retaining the blank execution-ID `FLEX_TXN:<transactionID>` fallback.

## Decisions

- Identity is scoped by canonical `account_id`.
- Protected fields are `instrument_id`, `side`, `quantity`,
  `trade_timestamp_utc`, `currency`, `price`, `commission`, `fees`, and
  `net_cash`, following the approved design.
- Decimal strings compare numerically, UUIDs compare as UUID values, and
  timestamps compare as UTC instants. Missing commission and fees compare equal
  to numeric zero, matching the broker cost normalization policy.
- `cost`, `realized_pnl`, `net_cash_in_base`, `fx_rate_to_base`, report date,
  functional currency, and description provenance remain refreshable derived or
  audit fields.
- A blank incoming transaction ID does not conflict with an existing nonempty
  one; two different nonempty transaction IDs do conflict.
- Validation is read-only and does not merge identities or modify canonical
  rows.

## TDD and verification

- Red: `tests/test_trade_consistency.py` initially failed collection because
  `app.db.trade_consistency` did not exist. After adding the DB validator, the
  three mapping sentinel cases failed because mapping accepted them.
- Green: `DATABASE_URL=postgresql+psycopg://postgres:implementation-only@127.0.0.1:32771/postgres .venv/bin/pytest -q tests/test_trade_consistency.py`
  passed: `8 passed`.
- Adjacent mapping: the focused test plus
  `tests/test_mapping_canonical_pipeline.py` passed: `60 passed`.
- Adjacent correction/pipeline regressions:
  `tests/test_import_corrections.py tests/test_jobs_canonical_pipeline.py`
  passed: `29 passed`.
- Ruff passed for all three owned code/test files.
- MyPy passed for `app/db/trade_consistency.py` and `app/mapping/service.py`.

## Integration concerns

- Task 1 owns the repository and pipeline hooks. The repository method must call
  the validator with its shared write `Connection`, after instrument IDs have
  been resolved and before the trade UPSERT. This keeps validation and canonical
  publication in the same semantic transaction.
- Live ingestion should opt in. Historical replay should keep validation off so
  previously accepted history is not reinterpreted.
- Exporting the error from `app.db.__init__` is optional; current tests and the
  intended repository integration can import directly from
  `app.db.trade_consistency`.
