# Broker-position reconciliation final-review fix report

## Scope and outcome

- Fix base: `f2a7f0b749d777dc583b42c3a015a732bbdb9452`
- Worktree: `/stock_app/.worktrees/broker-position-reconciliation`
- Branch: `codex/broker-position-reconciliation`
- Outcome: all eight final-review findings and both deferred minor findings are resolved.
- No schema migration or third-party dependency was added. The raw-record join was sufficient
  for historical execution multipliers.

## Finding resolutions

### 1. CRITICAL — ordinary HTTP replay could delete unsupported dates

- Split the public replay entry point from cleanup authority.
  `job_execute_reprocess_target()` now always passes
  `allow_unsupported_snapshot_cleanup=False`.
- Added the explicitly named operator-only
  `job_execute_reprocess_target_with_cleanup()` path. Its only production caller is the
  complete-scope CLI branch in `app/main.py`.
- Kept unscoped replay non-cleanup and kept cleanup after every selected artifact succeeds.
  The existing error-to-result behavior and API error mapping are unchanged.
- Added API/orchestrator/CLI regressions proving scoped HTTP, unscoped replay, and replay
  failure do not delete; the guarded complete-scope CLI path can still discover, record, and
  delete candidates.
- Updated the deterministic PostgreSQL cleanup scenario to invoke the operator-only method.

### 2. CRITICAL — exact matches used broker unrealized P&L

- Exact FIFO/broker quantity matches now retain FIFO cost and realized P&L, then calculate
  unrealized P&L as `broker quantity * mark * multiplier * FX - FIFO cost`.
- Broker `fifoPnlUnrealized` remains available only for broker/FIFO quantity mismatches and
  broker-only facts.
- Missing exact-match mark, positive multiplier, or required FX now produces zero provisional
  unrealized P&L with `EOD_MARK_MISSING_ALL_SOURCES`; it is not reported as a final broker
  unrealized value.
- The required regression proves quantity 10, mark 12, multiplier 1, FX 1, FIFO cost 100
  yields unrealized P&L 20 even when the broker supplies 200.
- The existing seeded fixture was not altered to invent a missing stock multiplier. Its
  assertion now records the resulting provisional uncertainty.

### 3. CRITICAL — option FIFO omitted contract multipliers

- Extended `LedgerTradeFillRecord` with typed `asset_category` and raw `multiplier` fields.
- The ledger trade read joins the account-owned instrument and the immutable source
  `raw_record`, normalizes the raw multiplier/close mark, and performs no raw mutation.
- Canonical mapping now requires a positive multiplier on every `OPT` execution, rejects any
  provided nonpositive multiplier for other categories, and rejects nonpositive trade FX.
- FIFO receives contract-scaled per-unit trade price and close/last-trade marks. Quantity,
  fees, commission, net cash, and net cash in base retain their prior total-value semantics.
- Non-options use a validated supplied multiplier and use identity 1 only when the optional
  value is absent.
- Unit/repository/mapping coverage includes missing/blank/sentinel/zero/negative option
  multipliers, normalized `1,000`, option close marks, and supplied non-option multipliers.
- The PostgreSQL ALEX regression now uses the evidenced multiplier 100 and asserts that the
  two-contract 0.60-to-0 option close contributes realized P&L 120 before fees.

### 4. CRITICAL — CASH/FX histories entered absence-as-zero reconciliation

- Added an account- and instrument-scoped repository read for canonical asset categories.
- Snapshot construction now classifies broker eligibility from canonical instrument metadata.
  `CASH` and `FX` retain the existing event-derived path even for a completed artifact.
- CASH/FX rows cannot increment broker match, mismatch, broker-only, or broker-absent counters,
  and broker absence cannot zero their event-derived positions.
- Unit coverage exercises both categories. A disposable PostgreSQL completed-artifact test
  proves an existing CASH event remains quantity 2 and nonprovisional with an empty
  OpenPositions section.

### 5. IMPORTANT — raw valuation SQL normalization differed from mapping

- Required `position` now trims whitespace, strips commas, and fails closed for blank or
  missing values.
- Optional mark, cost, broker unrealized, FX, and multiplier now trim whitespace, preserve
  `''`, `-`, `--`, and `N/A` as SQL `NULL`, strip comma separators, and still raise on malformed
  nonblank numeric text.
- Asset category/currency comparisons are trimmed and uppercased at the same boundary.
- SQL-contract assertions cover all six numeric fields. Disposable PostgreSQL coverage proves
  comma/whitespace normalization, all optional sentinel forms, missing required position, and
  malformed optional rejection.

### 6. IMPORTANT — runbook omitted legacy successful-owner artifacts

- Every repair selection/checksum/provisional query now mirrors production eligibility:
  a non-null completion pointer requires a successful completion run, while a legacy null
  pointer requires a successful immutable owner run.
- Per-date selection uses newest `created_at_utc`, then `raw_artifact_id`, exactly as the
  production selector does.

### 7. IMPORTANT — runbook could certify an empty or wrong scope

- Scope discovery now lists production-eligible selected artifacts and verifies each selected
  artifact contains OpenPositions.
- The recorded pre-delete report uses the production-supported dates and the identical
  explicit-scope owner-run cleanup predicate. It prints `ABORT_EMPTY_SELECTION` for no
  selection and `ABORT_MISSING_OPENPOSITIONS` for an incomplete selected artifact.
- Verification is bound to the same explicit account/period/query. It reports, for every
  selected date, expected broker OpenPositions count, three final snapshot discrepancy rows,
  and all four production reconciliation counters.
- The latest selected date explicitly reports whether the known Task 8 count is exactly 105.
- Missing per-date snapshot diagnostics are surfaced as
  `ABORT_MISSING_SNAPSHOT_DIAGNOSTIC` rather than disappearing from the result set.
- Provisional summaries and checksums are restricted to the same account and selected dates.
  The `REPAIR_ACCOUNT_ID` versus container `ACCOUNT_ID` guard remains mandatory.

### 8. IMPORTANT — runbook Compose commands could target another project/checkout

- The repair section defines one `repair_compose` wrapper using project `stock_app`, env file
  `/stock_app/.env`, and the reviewed worktree's Compose file.
- The runbook verifies the branch and reviewed commit before backup/deploy. All repair backup,
  query, build, execution, verification, and stop commands use the wrapper.
- `up -d --build app` therefore resolves build context relative to the reviewed worktree and
  addresses the existing project instead of starting a duplicate.
- An automated documentation test runs `bash -n` over every repair shell block and executes
  all five SQL heredocs against a fresh migrated PostgreSQL database.

### 9. DEFERRED MINOR — positive mapping regressions

- Added explicit OpenPositions zero/negative `fxRateToBase` and `multiplier` cases.
- Added trade zero/negative FX and option missing/blank/sentinel/zero/negative multiplier
  cases while implementing the multiplier contract.

### 10. DEFERRED MINOR — Task 4 stub docstring

- Documented `functional_currency` in the snapshot test stub's argument list.

## TDD evidence

### RED

The focused regressions were introduced before each corresponding production change:

- Cleanup isolation: 5 focused failures showed the safe scoped method still deleted, the
  operator-only method did not exist, and the CLI called the unsafe shared method.
- Exact-match economics: 5 of 6 focused cases failed because broker unrealized P&L was copied
  and missing mark/multiplier did not remain provisional.
- Multiplier economics: 7 mapping cases failed; the repository record lacked typed raw fields;
  and 6 strict snapshot cases failed before trade/mark scaling and option validation existed.
- CASH/FX: the metadata method was absent, the strict CASH/FX result was zeroed, and the
  completed-artifact PostgreSQL snapshot was quantity zero/provisional.
- SQL normalization initially failed both focused tests: the SQL-contract assertion exposed
  direct casts, and PostgreSQL rejected `" 1,234.5 "`. A follow-up missing-key RED proved
  PostgreSQL had silently returned NULL for a required missing `position` until the required
  expression used fail-closed `COALESCE`.
- Runbook: the base-document semantic check failed because the repair section lacked the
  explicit `stock_app` project binding, empty-selection abort, and 105-row check.

Representative captured RED results:

```text
tests/test_db_ledger_snapshot.py::test_open_position_query_normalizes_flex_numeric_text FAILED
tests/test_end_to_end_seeded.py::test_postgresql_open_position_numeric_normalization_matches_mapping FAILED
2 failed

follow-up missing required position:
2 failed (SQL contract did not contain COALESCE; PostgreSQL DID NOT RAISE)
```

### GREEN focused verification

Cleanup/API/CLI isolation:

```text
pytest -q tests/test_api_ingestion.py tests/test_jobs_reprocess.py \
  -k 'explicit_scope_overrides or scoped_reprocess_does_not_cleanup or default_reprocess_does_not_cleanup or failure_never_deletes or records_cleanup_candidates or cli_uses_explicit_cleanup or cli_without_complete_scope'
9 passed, 19 deselected in 0.61s
```

Economics, multiplier/ALEX, CASH/FX, SQL normalization, and docs parsing:

```text
pytest -q tests/test_mapping_canonical_pipeline.py tests/test_db_ledger_snapshot.py \
  tests/test_ledger_snapshot_service_strict.py tests/test_end_to_end_seeded.py \
  -k 'broker_mark or exact_match or market_input or option_execution or execution_multiplier or option_fifo or option_close_mark or non_option_fifo or cash_fx or cash_event or asset_category_map or raw_multiplier or numeric_normalization or runbook or non_positive_rate_or_multiplier or alex_assignment'
30 passed, 72 deselected in 2.38s
```

The broader touched-module gate also passed:

```text
pytest -q tests/test_api_ingestion.py tests/test_jobs_reprocess.py \
  tests/test_mapping_canonical_pipeline.py tests/test_db_ledger_snapshot.py \
  tests/test_ledger_snapshot_service_strict.py tests/test_end_to_end_seeded.py \
  tests/test_jobs_ingestion_orchestrator.py
149 passed in 4.37s
```

## Final required gates

All commands used `/stock_app/.venv`; test/MyPy commands loaded `/stock_app/.env` with
automatic export.

```text
pytest -q
232 passed in 7.59s

ruff check app/ tests/ --ignore=E501,W293,W291
All checks passed!

mypy
Success: no issues found in 60 source files

mypy --strict tests/test_mapping_canonical_pipeline.py tests/test_db_ledger_snapshot.py \
  tests/test_ledger_snapshot_service_strict.py tests/test_jobs_reprocess.py
Success: no issues found in 4 source files

python -m pip check
No broken requirements found.

git diff --check
PASS (no output)
```

## Changed files

- `app/db/interfaces.py` — typed multiplier/category ledger fields and instrument metadata port.
- `app/db/ledger_snapshot.py` — metadata/raw multiplier reads and canonical numeric SQL handling.
- `app/jobs/reprocess_orchestrator.py` — non-cleanup scoped replay plus operator-only cleanup path.
- `app/ledger/snapshot_service.py` — multiplier-aware FIFO/marks, exact-match economics, CASH/FX eligibility.
- `app/main.py` — complete-scope CLI dispatch to cleanup-capable path.
- `app/mapping/service.py` — option multiplier and positive FX/multiplier validation.
- `docs/operations.md` — safe pinned repair, selection, candidate, and verification runbook.
- `tests/test_api_ingestion.py` — HTTP cleanup isolation.
- `tests/test_db_ledger_snapshot.py` — typed read, metadata scope, and SQL normalization contracts.
- `tests/test_end_to_end_seeded.py` — ALEX 120, CASH preservation, SQL normalization, docs parsing, CLI cleanup.
- `tests/test_jobs_ingestion_orchestrator.py` — `functional_currency` docstring correction.
- `tests/test_jobs_reprocess.py` — safe/cleanup/failure/unscoped/CLI behavior.
- `tests/test_ledger_snapshot_service_strict.py` — exact economics, uncertainty, multiplier, CASH/FX coverage.
- `tests/test_mapping_canonical_pipeline.py` — positive FX/multiplier mapping coverage.
- `.superpowers/sdd/2026-08-21-broker-position-reconciliation/final-fix-report.md` — this report.

## Self-review

- Searched all callers of `job_execute_reprocess_target_with_cleanup`; `app/main.py` is the
  sole production caller. HTTP resolves only `job_execute_reprocess_target`.
- Confirmed unsupported discovery/delete repository predicates were not altered. Cleanup is
  still skipped for empty selections and executed only after all selected artifacts replay.
- Confirmed broker unrealized is reachable only after an exact-match branch is excluded.
- Confirmed multiplier scales per-unit FIFO trade economics and marks, while fees and stored
  cash values are untouched.
- Confirmed every instrument in snapshot scope must have account-scoped canonical category
  metadata; no cross-account metadata lookup is possible.
- Confirmed CASH/FX use event-derived quantity/valuation and do not increment broker counters.
- Confirmed no synthetic events/lots, raw updates, migration, blanket type ignores, MyPy
  weakening, or unrelated refactor was introduced.
- Confirmed runbook eligibility is repeated consistently in selection, checksum, and
  provisional queries, including legacy successful-owner artifacts.
- Confirmed all repair shell/SQL blocks parse and the SQL runs on a freshly migrated empty DB.
- `git diff --check` is clean and the complete final-review-focused plus full suite passed.

## Active-data safety and concerns

- No active application data was queried or mutated during this fix wave.
- No live IBKR endpoint or adapter was called.
- PostgreSQL integration tests created isolated disposable `test_*` databases, migrated them,
  and dropped them in `finally` blocks.
- No Docker Compose deployment, backup, or Task 8 cleanup command was executed. Active repair
  remains an operator step after this commit is reviewed and its backup/candidate evidence is
  recorded.
- Concerns: none known in the code fix. The expected operational prerequisite remains the
  verified Task 8 backup and explicit per-scope candidate report before the operator CLI.

## User-Authorized Targeted Fix Cycle

### Scope and resolution

- Cycle base: `e65309f0991010dffb88950c595f80da1e94e36c`.
- Residual defect: three documented PostgreSQL commands consumed host-shell
  `REPAIR_ACCOUNT_ID`, `TARGET_PERIOD`, and `TARGET_QUERY` inside a single-quoted
  container-side `sh -u` command without forwarding those variables through Compose exec.
- Root cause: single quotes correctly defer variable expansion to the container shell, but the
  absent `exec -e` arguments left that clean environment without the three scope variables.
- Fix: the pre-delete report, broker/discrepancy verification, and checksum/provisional command
  now each pass explicit `-e NAME="$NAME"` values before the `postgres` service. This works even
  though the host variables are shell variables rather than exported environment variables.
- Preserved without change: Compose project `stock_app`, `/stock_app/.env`, the reviewed
  worktree Compose file, account guard, SQL text and production-equivalent eligibility/scope
  predicates.

### RED evidence

The regression extracts all three affected Bash blocks, executes each through a Compose-like
boundary that starts the inner command with `env -i`, and uses a fake `psql` only at the
external database-client boundary. The fake client verifies that its `-v` values equal the
three variables actually present inside the simulated container.

Before the runbook fix:

```text
set -a; source /stock_app/.env; set +a
/stock_app/.venv/bin/pytest -q \
  tests/test_end_to_end_seeded.py::test_operations_repair_postgres_commands_forward_scope_to_container
FAILED test_operations_repair_postgres_commands_forward_scope_to_container
sh: 1: REPAIR_ACCOUNT_ID: parameter not set
1 failed in 0.73s
```

This is the original runtime failure, not a source-text or Bash-syntax assertion.

### GREEN evidence and final gates

```text
set -a; source /stock_app/.env; set +a
/stock_app/.venv/bin/pytest -q \
  tests/test_end_to_end_seeded.py::test_operations_repair_postgres_commands_forward_scope_to_container
1 passed in 0.65s

set -a; source /stock_app/.env; set +a
/stock_app/.venv/bin/pytest -q \
  tests/test_end_to_end_seeded.py::test_operations_repair_runbook_shell_and_sql_blocks_parse
1 passed in 1.21s

set -a; source /stock_app/.env; set +a
/stock_app/.venv/bin/pytest -q
233 passed in 7.81s

/stock_app/.venv/bin/ruff check app/ tests/ --ignore=E501,W293,W291
All checks passed!

set -a; source /stock_app/.env; set +a
/stock_app/.venv/bin/mypy
Success: no issues found in 60 source files

set -a; source /stock_app/.env; set +a
/stock_app/.venv/bin/mypy --strict tests/test_mapping_canonical_pipeline.py \
  tests/test_db_ledger_snapshot.py tests/test_ledger_snapshot_service_strict.py \
  tests/test_jobs_reprocess.py
Success: no issues found in 4 source files

/stock_app/.venv/bin/python -m pip check
No broken requirements found.

git diff --check
PASS (no output)
```

The shell/SQL documentation test runs `bash -n` on every repair Bash block and executes all
five SQL heredocs against a freshly created, migrated, disposable PostgreSQL database.

### Changed files and safety review

- `docs/operations.md` — forwards all three explicit scope variables into each of the three
  container-side PostgreSQL commands.
- `tests/test_end_to_end_seeded.py` — adds the host/container environment-boundary regression.
- `.superpowers/sdd/2026-08-21-broker-position-reconciliation/final-fix-report.md` — records this
  authorized cycle.
- No application code, SQL predicates, schema, dependencies, or unrelated documentation was
  changed.
- No active data was read or mutated, no repair/cleanup command was run, Docker Compose was not
  invoked against the active project, and no IBKR endpoint was contacted. Documentation SQL
  validation used only a disposable `test_*` database that the test dropped in `finally`.
- Concerns: none known.
