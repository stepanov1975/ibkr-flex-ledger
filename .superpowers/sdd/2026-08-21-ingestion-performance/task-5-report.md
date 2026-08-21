# Task 5 Report: Scoped Snapshot Repository and Service

## Status

Implemented the scoped ledger snapshot repository/service behavior on `codex/ingestion-performance` without orchestrator wiring.

## Implementation

- Extended `LedgerSnapshotRepositoryPort` with scope resolution and selected-instrument currency reads.
- Added optional instrument scope arguments to trade, cashflow, corporate-action, OpenPositions valuation, and open-lot reconciliation methods.
- Added optional currency scope to FX reads.
- Implemented one-query scope resolution as the union of affected conids and canonical instrument currencies.
- Normalized UUID-backed scope query results to `str`, matching request-layer instrument identifiers.
- Added conditional PostgreSQL array predicates to all scoped reads while leaving the `None` full-mode SQL and parameters unchanged.
- Limited scoped lot reconciliation to selected instruments for both stale-lot closure and replacement request persistence.
- Added the public service scope arguments with `None` defaults.
- Added the successful no-read no-op for two empty affected sets and the successful post-lookup no-op for an empty resolved scope.
- Built scoped FX inputs from affected source currencies, selected instrument currencies, and trade/cashflow currencies plus functional currencies.
- Propagated resolved IDs through all scoped reads and reconciliation, and filtered lot/snapshot requests before persistence.
- Preserved existing FIFO, valuation, FX resolution, and PnL calculation logic.

## TDD Evidence

### RED: service scope contract

Command:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_ledger_snapshot_service_strict.py -k 'resolved_scope or empty_scope or none_scope'
```

Output:

```text
FF.                                                                      [100%]
2 failed, 1 passed, 6 deselected in 0.27s
```

The failures were the expected missing-service-signature errors: unexpected `affected_conids` and too many positional arguments.

### RED: repository scope contract

Command:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_db_ledger_snapshot.py
```

Output:

```text
FFFF                                                                     [100%]
4 failed in 0.23s
```

The failures were the expected missing lookup methods and missing scoped read/reconciliation parameters.

### RED mutation check: unrelated persistence

After adding an unrelated trade fixture, the scope-filter block was temporarily removed and the focused test was run:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_ledger_snapshot_service_strict.py -k resolved_scope
```

Output:

```text
F                                                                        [100%]
1 failed, 8 deselected in 0.26s
```

The assertion showed the unrelated instrument lot request reaching reconciliation. Restoring the filter made the test pass.

### GREEN: focused repository, service, and query-template suites

Command:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_db_ledger_snapshot.py tests/test_ledger_snapshot_service_strict.py tests/test_db_query_templates.py
```

Output:

```text
...................                                                      [100%]
19 passed in 0.23s
```

## Verification

Focused ledger repository and strict snapshot tests:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_db_ledger_snapshot.py tests/test_ledger_snapshot_service_strict.py
.............                                                            [100%]
13 passed in 0.22s
```

Static checks:

```text
/stock_app/.venv/bin/ruff check app/db/interfaces.py app/db/ledger_snapshot.py app/ledger/snapshot_service.py tests/test_db_ledger_snapshot.py tests/test_ledger_snapshot_service_strict.py
All checks passed!

/stock_app/.venv/bin/mypy app/db/interfaces.py app/db/ledger_snapshot.py app/ledger/snapshot_service.py
Success: no issues found in 3 source files
```

Full suite, run once after focused verification:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q
........................................................................ [ 53%]
..............................................................           [100%]
134 passed in 3.90s
```

## Changed Files

- `app/db/interfaces.py`
- `app/db/ledger_snapshot.py`
- `app/ledger/snapshot_service.py`
- `tests/test_ledger_snapshot_service_strict.py`
- `tests/test_db_ledger_snapshot.py`
- `.superpowers/sdd/2026-08-21-ingestion-performance/task-5-report.md`

## Self-Review

- Scope semantics match the brief: only `None`/`None` is full mode; any non-full empty union is a no-op.
- Full-mode query predicates and parameters remain unchanged because scope clauses and array parameters are added only for non-`None` arguments.
- PostgreSQL `ANY` parameters are passed as lists for psycopg array adaptation while public repository contracts remain tuples.
- Scoped FX selection is deterministic and sorted.
- Repository and service tests cover SQL predicates, UUID result normalization, argument propagation, the complete FX union, empty no-op behavior, full compatibility defaults, and exclusion of unrelated lot/snapshot requests.
- No orchestrator code or unrelated calculation/refactoring was changed.

## Concerns

None. Task 6 still needs to wire the affected scope into the orchestrator, as intentionally excluded from this task.
