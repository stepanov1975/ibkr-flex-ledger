# Task 6 Report: Ingestion Short-circuit, Delta Integration, and Orchestrator Timings

## Status

Implemented and verified on `codex/ingestion-performance`.

## Implementation

- Added monotonic integer-millisecond diagnostics for preflight, XML extraction, artifact persistence, raw persistence, canonical changed-row reads, canonical mapping/persistence, and snapshot work.
- Kept UTC timestamps for ingestion-run lifecycle persistence; all new duration subtraction uses `time.perf_counter_ns()` through `_duration_ms`.
- Added the `xml_extraction` started/completed timeline stage without renaming existing stages.
- Exact duplicate artifacts still complete fetch, preflight, extraction, SHA-256 calculation, and artifact identity UPSERT, then:
  - do not construct raw persistence requests;
  - do not call raw-row insertion;
  - do not read canonical raw rows or call canonical persistence;
  - do not derive incremental scope or call the configured snapshot service;
  - emit zero work counts/durations and the exact `exact_duplicate_artifact` skip reasons.
- Distinct artifacts construct and persist every extracted raw row, then use `db_raw_record_list_changed_for_run` for normal canonical mapping.
- Derived snapshot scope from the changed rows:
  - non-empty safe scope calls the snapshot service with frozen conids/currencies and records `incremental`;
  - unsafe scope performs an argument-compatible full snapshot call and records `full_fallback` plus the exact scope reason;
  - empty scope returns a zero-count result without calling the snapshot service and records `skipped`.
- Preserved optional-dependency compatibility:
  - a configured snapshot service still receives the established full call when canonical persistence is absent;
  - a missing snapshot service retains `snapshot_service_not_configured` and records a skipped zero-duration stage;
  - an exact duplicate still suppresses a configured snapshot when canonical persistence is absent;
  - a missing snapshot service keeps its established skip reason even for an exact duplicate.
- Left the reprocess orchestrator unchanged, so it continues to use complete-period raw reads and full replay behavior.

## TDD Evidence

### RED: duplicate, delta, fallback, and duration contracts

Command:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_jobs_ingestion_orchestrator.py -k 'exact_duplicate or incremental_scope or full_snapshot or monotonic_durations'
```

Output:

```text
FFFF.                                                                    [100%]
4 failed, 1 passed, 9 deselected in 0.36s
```

The failures were the intended missing behaviors: duplicate raw insertion was called, the changed-row reader was not called, snapshot scope diagnostics were absent, and monotonic preflight timing was absent. The already-existing canonical-absent/full-snapshot compatibility behavior passed.

### GREEN: primary focused contracts

Command:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_jobs_ingestion_orchestrator.py -k 'exact_duplicate or incremental_scope or full_snapshot or monotonic_durations'
```

Output:

```text
.....                                                                    [100%]
5 passed, 9 deselected in 0.31s
```

### RED/GREEN mutation check: empty snapshot scope

The empty-scope no-op branch was temporarily mutated to call the configured snapshot service.

Command:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_jobs_ingestion_orchestrator.py -k empty_changed_row_scope
```

Output:

```text
F                                                                        [100%]
1 failed, 15 deselected in 0.36s
```

The expected assertion showed `snapshot.build_calls == 1` instead of `0`. Restoring the no-op implementation and running the compatibility selection produced:

```text
...                                                                      [100%]
3 passed, 13 deselected in 0.30s
```

## Verification Commands and Output

Orchestrator and reprocess suites:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_jobs_ingestion_orchestrator.py tests/test_jobs_reprocess.py
..................                                                       [100%]
18 passed in 0.30s
```

Relevant adapter, scope, and snapshot suites:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_adapters_flex_web_service.py tests/test_adapters_flex_error_codes.py tests/test_jobs_incremental_scope.py tests/test_ledger_snapshot_service_strict.py
..............................                                           [100%]
30 passed in 0.42s
```

Final combined focused gate after the compatibility additions:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_jobs_ingestion_orchestrator.py tests/test_jobs_reprocess.py tests/test_adapters_flex_web_service.py tests/test_adapters_flex_error_codes.py tests/test_jobs_incremental_scope.py tests/test_ledger_snapshot_service_strict.py
..................................................                       [100%]
50 passed in 0.47s
```

Ruff on both touched Python files:

```text
/stock_app/.venv/bin/ruff check app/jobs/ingestion_orchestrator.py tests/test_jobs_ingestion_orchestrator.py
All checks passed!
```

Strict MyPy on touched production code:

```text
/stock_app/.venv/bin/mypy app/jobs/ingestion_orchestrator.py
Success: no issues found in 1 source file
```

MyPy on the touched test file, with narrow exemptions for its pre-existing untyped stubs and intentionally incomplete protocol doubles:

```text
/stock_app/.venv/bin/mypy --allow-untyped-defs --disable-error-code=arg-type --disable-error-code=attr-defined tests/test_jobs_ingestion_orchestrator.py
Success: no issues found in 1 source file
```

A strict combined MyPy invocation was also run and reported 36 existing errors in `tests/test_jobs_ingestion_orchestrator.py`, primarily `no-untyped-def`, incomplete `IngestionRunRepositoryPort`/canonical/snapshot test doubles, and `object`-typed diagnostics. No production error was reported; rewriting the legacy test module's unrelated typing surface was kept out of Task 6.

Full suite, run once after final focused/static verification:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q
........................................................................ [ 50%]
......................................................................   [100%]
142 passed in 3.79s
```

Final whitespace validation:

```text
git diff --check
```

Output: clean.

## Changed Files

- `app/jobs/ingestion_orchestrator.py`
- `tests/test_jobs_ingestion_orchestrator.py`
- `.superpowers/sdd/2026-08-21-ingestion-performance/task-6-report.md`

## Self-Review

- Every changed production branch traces to the Task 6 short-circuit, delta read, scope, timing, or optional-dependency compatibility requirements.
- Exact duplicates cannot reach raw request construction because the request list exists only in the distinct-artifact branch.
- Exact duplicates set canonical input/read/mapping counts and durations to zero and bypass the scope builder before snapshot handling.
- Normal distinct ingestion uses only `db_raw_record_list_changed_for_run`; `db_raw_record_list_for_run` remains untouched for reprocess callers.
- The scope builder receives the same changed-row objects used by canonical mapping, so snapshot scope is derived from persisted semantic input rather than all extracted rows.
- Only an explicit unsafe scope (or the compatibility case where canonical persistence is not configured) performs a full snapshot call; safe empty scope is a no-op.
- Snapshot service absence is checked before duplicate/scope branching, preserving its established diagnostic precedence.
- All requested duration keys are emitted as non-negative integers on their completed configured stages; skipped work uses zero.
- No repository transaction boundary, error-code mapping, external adapter behavior, or reprocess code was changed.

## Concerns

- `tests/test_jobs_ingestion_orchestrator.py` does not pass the repository's strict default MyPy settings because of pre-existing untyped and intentionally incomplete test doubles. Production MyPy is clean, and the touched test behavior is clean under narrow baseline exemptions documented above.
