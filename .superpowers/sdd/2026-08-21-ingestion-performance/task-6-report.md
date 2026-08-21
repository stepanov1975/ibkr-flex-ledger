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

A strict comparison performed during fix round 1 showed that this statement was incorrect: the Task 6 base had 26 findings and commit `a378086` had 35. The added findings included Task 6 test-double annotations and protocol call-site incompatibilities; they were not all pre-existing. Fix round 1 corrects the touched test doubles and brings strict MyPy on this file to zero findings.

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

None after fix round 1. The original strict-MyPy concern and its inaccurate attribution are corrected below.

## Fix Round 1: Failed-owner Recovery and Strict MyPy

### Root Cause

`db_raw_artifact_upsert` and `db_raw_record_insert_many` use separate database transactions. The original Task 6 short-circuit treated `RawArtifactPersistResult.deduplicated=True` as proof that the earlier run had also committed raw rows and completed semantic work. A failure after the artifact transaction could therefore leave an artifact owned by a failed run, and every retry would skip the missing raw insertion permanently.

The raw-row uniqueness key is artifact/section/source identity and the raw batch is atomic. A failed-owner retry can safely attempt the entire batch:

- a nonzero insert means the retry supplied previously missing rows, so canonical work reads the current run's changed rows;
- an all-conflict result means the failed owner already committed the raw batch, so canonical work reads all rows for the artifact-owning run;
- only an artifact owner whose persisted ingestion-run status is `success` is a completed exact duplicate.

When owner rows are reused, the snapshot call also receives the owner run ID so owner-scoped OpenPositions valuation context is available. Existing exact duplicate skip diagnostics apply only to the owner-success path. Optional canonical/snapshot service behavior remains unchanged.

### Implementation

- Read the artifact owner's ingestion run through `db_ingestion_run_get_by_id` for every deduplicated artifact.
- Preserve the original raw/canonical/snapshot short-circuit only when the owner state is `success`.
- Attempt raw insertion for a failed or otherwise non-success owner.
- Select `db_raw_record_list_changed_for_run(current_run_id)` when retry insertion adds rows.
- Select `db_raw_record_list_for_run(artifact_owner_run_id)` when retry insertion is all conflicts.
- Carry the selected semantic run ID into the snapshot service.
- Made the Task 6-modified repository, raw, canonical, and snapshot test doubles fully typed and protocol-compatible. Strict MyPy on the touched test file now reports zero findings, versus 26 at the base and 35 at the original Task 6 head.

### TDD Evidence

RED command:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_jobs_ingestion_orchestrator.py -k 'exact_duplicate_skips_raw_canonical_and_snapshot_work or failed_owner'
```

Output:

```text
FFF                                                                      [100%]
3 failed, 15 deselected in 0.38s
```

All failures showed that `repository.get_by_id_calls` was empty. The owner-success path never validated success, and both failed-owner retries therefore had no recovery-state decision.

GREEN command:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_jobs_ingestion_orchestrator.py -k 'exact_duplicate_skips_raw_canonical_and_snapshot_work or failed_owner'
```

Output:

```text
...                                                                      [100%]
3 passed, 15 deselected in 0.31s
```

The two recovery tests execute two sequential runs and assert owner-state reads, raw insertion calls and run IDs, current changed-row versus owner all-row selection, canonical persistence calls, snapshot calls, and snapshot run ID.

### Verification

Complete ingestion orchestrator file:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_jobs_ingestion_orchestrator.py
..................                                                       [100%]
18 passed in 0.31s
```

Ingestion and reprocess suites:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_jobs_ingestion_orchestrator.py tests/test_jobs_reprocess.py
......................                                                   [100%]
22 passed in 0.31s
```

Scope and snapshot suites:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q tests/test_jobs_incremental_scope.py tests/test_ledger_snapshot_service_strict.py
..............                                                           [100%]
14 passed in 0.32s
```

Ruff and configured production/test MyPy:

```text
/stock_app/.venv/bin/ruff check app/jobs/ingestion_orchestrator.py tests/test_jobs_ingestion_orchestrator.py
All checks passed!

/stock_app/.venv/bin/mypy app/jobs/ingestion_orchestrator.py
Success: no issues found in 1 source file

/stock_app/.venv/bin/mypy tests/test_jobs_ingestion_orchestrator.py
Success: no issues found in 1 source file
```

Strict base comparison using MyPy's shadow-file support:

```text
/stock_app/.venv/bin/mypy --shadow-file tests/test_jobs_ingestion_orchestrator.py <(git show ca100d8:tests/test_jobs_ingestion_orchestrator.py) tests/test_jobs_ingestion_orchestrator.py
Found 26 errors in 1 file (checked 1 source file)
```

The original Task 6 head (`a378086`) produced `Found 35 errors in 1 file`; the corrected working tree produces zero. Thus the Task 6 diff adds no strict-MyPy findings relative to its base.

Full suite, run once after final focused and static verification:

```text
set -a; source /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q
........................................................................ [ 50%]
........................................................................ [100%]
144 passed in 3.95s
```

### Fix-round Self-Review

- Artifact identity is no longer used as a completion signal; persisted owner-run success is required.
- The raw retry remains one atomic batch and uses its inserted count to select the only two durable recovery histories.
- Recovery after raw failure reads only the current run delta; recovery after later semantic failure reads the failed owner's complete raw rows.
- Reused owner rows and snapshots use the same owner run ID.
- Owner-success duplicates retain exact skip counts, durations, and reasons.
- Missing canonical or snapshot services retain their established compatibility branches.
- Reprocess remains unchanged and continues to use complete-period reads.
- No repository schema, transaction, API, or adapter behavior changed.

### Fix-round Concerns

None.
