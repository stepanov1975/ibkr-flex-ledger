# Task 2 Report: Raw-row Delta Indexes and Immediate-predecessor Selection

## Implementation

- Added Alembic revision `20260821_04`, with reversible indexes for run-ordered raw-row reads and prior-version lookup.
- Extended `RawRecordReadRepositoryPort` with `db_raw_record_list_changed_for_run`.
- Added a single PostgreSQL `LEFT JOIN LATERAL` query that compares each row to the immediately preceding distinct ingestion run for its account, query, section, and source-row reference. Ordering is deterministic on `created_at_utc` then `raw_record_id`.
- Preserved the existing all-row run and period reads for reprocessing.
- Added database-backed migration upgrade/downgrade assertions and a four-version regression test that confirms a value reverting to an earlier value is still selected when it differs from its immediate predecessor.

## Changed files

- `alembic/versions/20260821_04_ingestion_performance_indexes.py`
- `app/db/interfaces.py`
- `app/db/canonical_persistence.py`
- `tests/test_db_migrations.py`
- `tests/test_db_canonical_upsert.py`
- `.superpowers/sdd/2026-08-21-ingestion-performance/task-2-report.md`

## RED verification

Each command loaded `/stock_app/.env` into its test process without printing it.

Migration RED command:

```bash
set -a; . /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_db_migrations.py -k ingestion_indexes
```

Output:

```text
FAILED tests/test_db_migrations.py::test_migrations_apply_and_are_idempotent_ingestion_indexes
1 failed, 2 deselected in 0.74s
```

The expected failure was `KeyError: 'ix_raw_record_run_created_id'`, before revision `20260821_04` existed.

Delta-read RED command:

```bash
set -a; . /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_db_canonical_upsert.py -k immediate_predecessor
```

Output after correcting the test fixture's PostgreSQL type inference:

```text
FAILED tests/test_db_canonical_upsert.py::test_changed_rows_compare_with_immediate_predecessor
1 failed, 4 deselected in 0.81s
```

The expected failure was `AttributeError` for the missing `db_raw_record_list_changed_for_run` method.

## GREEN verification

Migration focused command:

```bash
set -a; . /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_db_migrations.py -k ingestion_indexes
```

Output:

```text
1 passed, 2 deselected in 1.03s
```

Delta-read focused command:

```bash
set -a; . /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_db_canonical_upsert.py -k immediate_predecessor
```

Output:

```text
1 passed, 4 deselected in 0.75s
```

Required migration/canonical command:

```bash
set -a; . /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_db_migrations.py tests/test_db_canonical_upsert.py
```

Output:

```text
8 passed in 2.58s
```

Full-suite command:

```bash
set -a; . /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q
```

Output:

```text
116 passed in 4.69s
```

## Self-review

- The new prior-version index aligns with the lateral query's equality keys and descending predecessor ordering.
- The run-order index aligns with deterministic current-run output ordering.
- The predecessor query excludes rows from the same ingestion run and selects only the immediate prior row, so a value changed back to an older value remains selected.
- `IS DISTINCT FROM` supplies null-safe JSONB payload comparison.
- Migration downgrade removes exactly the two new indexes, and the migration test verifies this on an isolated temporary database.
- Existing period and all-row run queries are unchanged.
- `git diff --check` completed without output.

## Concerns

No functional concerns. The supplied migration test's name did not match the required `-k ingestion_indexes` command, so the test name was extended to make that required command execute it. The supplied raw-artifact fixture reused `:sha` as text and `bytea`, which PostgreSQL rejects; both fixture uses are cast to `bytea` without changing the test's behavior.
