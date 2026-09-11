# IBKR Import Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make IBKR imports atomic, explicit about inconsistent source data, and recoverable after realistic configuration, process and transport failures.

**Architecture:** Retain current repositories and orchestrators. Add a shared DB transaction for semantic publication and a session advisory lock for whole-run ownership. Persist raw evidence before validating new report context; reject live execution conflicts instead of repairing them.

**Tech Stack:** Python, SQLAlchemy/psycopg, PostgreSQL 17, httpx, pytest, Alembic.

**Spec:** `docs/superpowers/specs/2026-09-11-ibkr-import-reliability-design.md`

## Global Constraints

- Keep all SQL in `app/db`; never import runtime code from `references/`.
- Preserve raw payloads and rows; no production-data rewrites or destructive cleanup.
- Reject inconsistent execution identities and protected economics; do not merge them automatically.
- Keep historical successful-source replay and manual split approvals working.
- Tests use synthetic reports and temporary PostgreSQL only; no live IBKR calls or external alerts.
- Preserve token redaction and bounded retry budgets.
- Match existing style; no unrelated refactoring or new dependencies.

## Task 1: Atomic semantic publication and retained failure evidence

**Files:** `app/db/session.py`, `app/db/canonical_persistence.py`, `app/db/raw_persistence.py`, `app/db/ingestion_run.py`, `app/db/ledger_snapshot.py`, `app/db/interfaces.py`, `app/jobs/ingestion_orchestrator.py`, `app/jobs/reprocess_orchestrator.py`, new `tests/test_ingestion_atomicity.py`.

**Interfaces:** `db_connection_scope(engine: Engine, write: bool = False) -> ContextManager[Connection]`; `db_transaction_scope(engine: Engine) -> ContextManager[None]`; canonical repository `db_canonical_transaction() -> ContextManager[None]`. The execution-local scope joins only repositories sharing the same engine and resets in a finally block. Explicit ledger connections still win. Unit-test doubles implement null contexts; production repositories always use the real transaction.

- [x] Add PostgreSQL regressions showing that snapshot failure leaves canonical values/lots/snapshots unchanged and raw artifacts retained, and that a later partial report cannot publish a failed correction. Add equivalent replay/finalization failure tests.
```python
assert successful_import().status == "success"
assert import_with_snapshot_failure().status == "failed"
assert stored_trade_price() == Decimal("100")
assert retained_artifact_count() == 2
assert later_report_without_trades().status == "success"
assert stored_cost_basis() == Decimal("201")
```
- [x] Run these tests and confirm current partial commits fail the assertions.
- [x] Add the DB connection/transaction scopes and route canonical/raw/run/ledger repository operations through them. Wrap canonical processing through successful finalization in ingestion/replay; keep download, immutable raw persistence and failed-run finalization outside.
```python
with canonical_repository.db_canonical_transaction():
    map_and_persist()
    build_snapshots()
    mark_artifact_completed()
    finalize_success()
```
- [x] Extract report rows/date on a best-effort basis, persist raw bytes even if parsing/preflight fails, then report the original validation failure. Do not allow an invalid artifact into canonical processing or replay.
- [x] Run affected tests, update only old assertions expecting partial commits, inspect diff and commit the task.

## Task 2: Conservative execution consistency checks

**Files:** new `app/db/trade_consistency.py`, `app/mapping/service.py`, new `tests/test_trade_consistency.py`; integration hooks in `app/db/canonical_persistence.py`, `app/db/interfaces.py`, `app/jobs/canonical_pipeline.py` are coordinated with Task 1.

**Interfaces:** `db_validate_trade_consistency(connection: Connection, requests: list[CanonicalTradeFillUpsertRequest]) -> None` raises `TradeConsistencyError(ValueError)` with code `TRADE_CONSISTENCY_CONFLICT`, identity, conflicting fields and raw source IDs. Repository `db_canonical_validate_trade_fills(requests)` exposes this function. `job_canonical_map_and_persist(..., validate_trade_consistency: bool = False)` calls it after resolving instrument IDs, before bulk event UPSERT; normal ingestion opts in, historical replay does not reinterpret accepted history.

- [x] Add real DB tests for one transaction changing execution ID; one execution changing a nonempty transaction ID; changed instrument/side/quantity/time/currency/price/commission/fees/net cash; conflicting duplicates in a single batch; unchanged equivalent decimal formatting; derived valuation fields changing without changing execution economics.
```python
with pytest.raises(TradeConsistencyError, match="ib_exec_id"):
    validate(second_report_with_new_execution_id)
assert canonical_execution_count() == 1
```
- [x] Add mapping regression: explicit `ibExecID="-"`, `"--"`, or `"N/A"` fails; genuine blank ID with EXECUTION and transactionID keeps the existing BookTrade fallback.
- [x] Run the failing tests, implement batched identity lookups and normalized comparisons, and avoid automatic identity reconciliation or a new correction UI.
- [x] Preserve replay regressions by explicitly seeding historically accepted corrections when their purpose is old-history replay. Update live correction tests to assert the new rejection contract for protected fields; preserve derived-field refresh tests.
- [x] Run focused tests, inspect diff and commit the task.

## Task 3: Validate new report account and base currency

**Files:** new `app/jobs/report_context.py`, `app/db/raw_persistence.py`, `app/db/interfaces.py`, `app/jobs/ingestion_orchestrator.py`, new `tests/test_report_context.py`, affected realistic ingestion fixtures.

**Interfaces:** `job_validate_report_context(payload_bytes: bytes, expected_broker_account_ids: frozenset[str] = frozenset()) -> str` returns the validated broker account ID. Raise a ValueError subtype with stable `REPORT_CONTEXT_INVALID` code on missing/mixed/inconsistent metadata or non-USD base. Raw repository `db_raw_successful_broker_account_ids(account_id: str) -> frozenset[str]` reads only successful source metadata.

- [x] Add unit tests for single valid USD account, linked accounts, conflicting header/row IDs, missing account metadata, non-USD base, and changing broker account after a previous successful import.
```python
assert job_validate_report_context(valid_usd_report) == "U123"
with pytest.raises(ValueError):
    job_validate_report_context(eur_base_report)
```
- [x] Run tests to confirm missing validation; implement the validator and successful-history lookup. Call after raw persistence and before canonical writes. Do not compare broker ID directly with an arbitrary internal account label.
- [x] Update live integration fixtures to include realistic AccountInformation; preserve explicitly malformed fixtures and old replay-only rows. Test that a rejected report remains stored but never becomes replayable.
- [x] Run focused tests, inspect diff and commit the task.

## Task 4: Whole-run ownership and bounded recovery state

**Files:** `app/db/ingestion_run.py`, `app/db/interfaces.py`, `app/jobs/ingestion_orchestrator.py`, `app/jobs/reprocess_orchestrator.py`, `app/db/canonical_persistence.py`, new Alembic migration after `20260910_15`, new `tests/test_ingestion_run_recovery.py`.

**Interfaces:** run repository `db_ingestion_run_guard(account_id: str) -> ContextManager[None]`. Hold the existing account advisory-key pair as a session lock on a dedicated connection, and use that same session for publication so losing ownership also prevents a commit. A current execution guard lets create_started avoid reacquiring the same lock on another connection. Release or invalidate the owning connection in finally; never return a locked session to the pool. Add `semantic_atomic boolean NOT NULL DEFAULT false` to run audit rows, marking only new guarded atomic workflows true.

- [x] Add PostgreSQL tests for concurrent exclusion, process/connection loss releasing ownership, recovery of leftover started rows, lock release after exceptions, and manual split lock compatibility. Do not use elapsed-time takeover.
```python
with first.db_ingestion_run_guard("ACCOUNT"):
    with pytest.raises(IngestionRunAlreadyActiveError):
        with second.db_ingestion_run_guard("ACCOUNT"):
            pass
```
- [x] Implement the guard and call it around full ingestion and replay execution. Under acquired ownership finalize leftover started rows as failed with `INGESTION_RUN_INTERRUPTED` diagnostics, retaining their raw artifacts.
- [x] Add migration and new-run atomic marker. Change incremental safety to consider failed non-atomic legacy runs only; test that new network/validation failures do not disable duplicate skipping and old potentially partial failures remain conservative.
- [x] Run lifecycle/concurrency/migration tests, inspect diff and commit the task.

## Task 5: Bounded transient HTTP retries

**Files:** `app/adapters/flex_web_service.py`, new `tests/test_flex_transport_reliability.py`.

**Interfaces:** Preserve public adapter API, typed error classifications and logical SendRequest/GetStatement retry budgets. Retain the existing three-attempt transport budget. Apply backoff for transient transport failures and HTTP 429/502/503/504. Parse Retry-After seconds or HTTP-date; if it exceeds the configured maximum wait, fail clearly rather than retry too early or sleep without a bound.

- [x] Use httpx.MockTransport to test 503 then success, connection/read failures then success, exhaustion, no retry for 400/401/403, Retry-After seconds/date, malformed header fallback, excessive Retry-After, retained statement reference, and token-safe errors.
```python
assert fetch_after_transient_503().payload_bytes == statement
assert requested_statement_reference_codes == ["REF", "REF"]
```
- [x] Run failing tests, implement retries using existing backoff configuration and redacted errors, preserve immediate terminal failure classification.
- [x] Run adapter tests, inspect diff and commit the task.

## Task 6: Integration, operator guidance and review

**Files:** `README.md`, `MVP_spec_freeze.md`, `docs/operations.md`, relevant tests and this plan.

- [x] Document strict live execution conflicts, allowed derived-field refreshes, required query metadata, automatic abandoned-run recovery, retained failed payloads, transport budgets, and the legacy recovery limitation. No live broker call, production repair, deploy or automatic acceptance of conflicting data.
- [x] Run the complete suite on temporary PostgreSQL, `.venv/bin/ruff check app tests`, and `.venv/bin/mypy`. Investigate failures without weakening assertions unrelated to the changed contracts.
- [x] Review the whole diff for scope, raw immutability, transaction/lock cleanup, replay compatibility and diagnostic usability. Fix confirmed issues and rerun affected checks.
- [x] Mark tasks complete with verification results; leave a reviewable feature branch without deployment.

## Verification and review results

Completed on `codex/ibkr-import-reliability` on 2026-09-11.

- Final full suite: **731 passed** on an isolated PostgreSQL 17 instance (84.06s).
- Full `ruff check app tests` passed; `mypy` passed for all 73 source files; diff whitespace checks passed.
- Each task passed spec and quality review. Final branch review approved after its late optional transaction-ID finding was fixed; 18 focused tests independently verified that fix.
- Review also found and resolved PostgreSQL numeric rounding false conflicts and loss of COMMIT acknowledgement incorrectly downgrading a durable success.
- Successful raw identity evidence participates in validation without rewriting canonical execution identities. Failed reports do not establish trusted identities.
- Existing legacy partial-write and historical-correction read-model tests use explicit legacy fixtures; current atomic behavior has separate live-ingestion regressions.
- No live IBKR requests, production-data repairs, deployment, merge, or push occurred. The temporary PostgreSQL container was removed after verification.

Before deploying, stop existing ingestion/replay workers, apply the Alembic migration,
and restart the workers together. Legacy failed imports retain conservative skip
behavior until their data has been investigated using verified sources.
