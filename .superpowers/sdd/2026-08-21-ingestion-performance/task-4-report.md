# Task 4 report: Pure incremental snapshot scope derivation

## Implementation

- Added `IncrementalSnapshotScope` as a frozen dataclass containing immutable conid and currency sets plus an optional full-rebuild reason.
- Added `job_build_incremental_snapshot_scope`, which unions normalized conids for recognized event sections, uppercases source currencies for `ConversionRates`, ignores irrelevant sections, and returns the specified reason for unscopable relevant rows.
- Added focused tests covering unioning, missing relevant keys, and irrelevant sections.

## TDD evidence

- RED: `pytest -q tests/test_jobs_incremental_scope.py` failed during collection with `ModuleNotFoundError: No module named 'app.jobs.incremental_scope'`.
- GREEN: After the minimal implementation, the focused file passed: `3 passed`.

## Commands and output

- `set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_jobs_incremental_scope.py` → `3 passed in 0.33s`
- `set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q` → `125 passed in 4.13s`
- `git diff --check` → clean.

## Files

- `app/jobs/incremental_scope.py`
- `tests/test_jobs_incremental_scope.py`

## Self-review

- Scope derivation is pure: it only reads input rows and returns a new frozen value.
- Relevant sections fail closed with the exact required reason and empty scope.
- No database or orchestrator code was changed.

## Concerns

None.
