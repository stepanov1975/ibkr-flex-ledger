# Task 2 Report: Persisted Broker Position Read Contract

## Status

DONE. Commit created: `6627fc1 feat: expose complete broker position facts`.

## Implementation

- Expanded frozen `LedgerOpenPositionValuationRecord` with `asset_category`, `currency`, `cost_basis_money`, `fx_rate_to_base`, and `multiplier`; made `mark_price` optional.
- Expanded the OpenPositions SQL projection to parse all broker valuation facts, normalize category/currency to uppercase, and preserve optional blank numeric values as SQL `NULL`.
- Preserved the account/run owner scope, optional instrument-ID predicate, OpenPositions row-reference filter, non-CASH/non-FX filter, and newest-`raw_record_id`-per-instrument ranking.
- Mapped SQL `NULL` optional values to Python `None`; nonblank numeric values continue to use database numeric casts so malformed values remain errors.
- Updated existing strict-service fixtures to supply the expanded value-object fields.

## TDD evidence

### RED

Added the focused option-field and blank-optional mapping tests in `tests/test_db_ledger_snapshot.py`, then ran:

```text
$ source /stock_app/.env && /stock_app/.venv/bin/pytest -q tests/test_db_ledger_snapshot.py -k open_position
FF                                                                       [100%]
2 failed, 4 deselected in 0.21s
```

The failures were the expected missing `asset_category` constructor field and the existing mapper returning string `"None"` for a blank mark price.

### GREEN

After the minimal implementation:

```text
$ source /stock_app/.env && /stock_app/.venv/bin/pytest -q tests/test_db_ledger_snapshot.py -k open_position
..                                                                       [100%]
2 passed, 4 deselected in 0.21s
```

## Verification

```text
$ /stock_app/.venv/bin/pytest -q tests/test_db_ledger_snapshot.py
6 passed in 0.23s

$ /stock_app/.venv/bin/mypy app/db/interfaces.py app/db/ledger_snapshot.py
Success: no issues found in 2 source files

$ /stock_app/.venv/bin/pytest -q tests/test_ledger_snapshot_service_strict.py
9 passed in 0.23s

$ IBKR_FLEX_TOKEN=compose-placeholder-token IBKR_FLEX_QUERY_ID=compose-placeholder-query /stock_app/.venv/bin/pytest -q
157 passed, 14 skipped in 1.58s
```

The first full-suite attempt without the documented placeholder Flex credentials had six failures: three unrelated settings failures due to missing `IBKR_FLEX_TOKEN`/`IBKR_FLEX_QUERY_ID`, and three expected constructor failures in existing strict-service fixtures. The fixtures were updated with the new required fields, then the full suite passed with the credentials supplied in-process; no environment files were changed.

## Changed files

- `app/db/interfaces.py`
- `app/db/ledger_snapshot.py`
- `tests/test_db_ledger_snapshot.py`
- `tests/test_ledger_snapshot_service_strict.py`

`app/db/__init__.py` already exported `LedgerOpenPositionValuationRecord`; no export edit was necessary.

## Self-review and fix evidence

- Reviewed the final diff and verified `git diff --check` passed.
- Confirmed no reconciliation, cleanup, or unrelated production behavior was added.
- Confirmed the SQL has no remaining stock-only predicate and includes the required asset/category, cost, FX, multiplier, and raw-record ranking clauses.
- Fixed the three downstream test fixtures exposed by the expanded required constructor contract; targeted strict tests and the full suite passed afterward.
- Worktree is clean after commit.

## Concerns

None for Task 2. Full-suite configuration requires Flex credentials; the successful run used only the documented placeholder values in the command environment.
