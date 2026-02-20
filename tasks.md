# Task Plan
Implement Task 7 from `implementation_task_list.md`: deliver a stocks-first FIFO ledger and deterministic daily PnL snapshots using existing canonical event data and Task 6 valuation/FX outputs. Snapshot computation must run automatically at the end of successful ingestion, and snapshot read APIs must be exposed in Task 7. The implementation should extend current project-native contracts (not reference code), keep SQL in `app/db` only, and reuse existing orchestrator patterns used by ingestion/reprocess. Work must proceed one milestone at a time, with each milestone ending in a verifiable artifact (tests, interfaces, service behavior, or wiring). If interrupted, resume by selecting the first subtask with `status: planned`, setting it to `in progress`, completing all checklist items, then marking it `done` and filling its Summary.

## Subtasks
1. **Freeze Task 7 implementation boundaries and reuse map** — `status: done`
	- **Description:** Confirm the exact extension points for Task 7 by auditing existing ledger/db/job contracts and approved references, then lock the minimal-diff implementation path.
	- [ ] Re-audit existing contracts in `app/ledger/interfaces.py`, `app/db/interfaces.py`, `app/jobs/interfaces.py`, and `app/bootstrap.py` to avoid duplicate abstractions.
	- [ ] Map existing reusable logic from `app/domain/flex_parsing.py`, `app/mapping/service.py`, and canonical persistence services that can be reused for Task 7 inputs.
	- [ ] Record the concrete module touchpoints for FIFO computation, lot persistence, snapshot persistence, and execution wiring.
	- **Summary:** Reuse map frozen with minimal-diff touchpoints: (1) trigger snapshot compute at ingestion success tail in `app/jobs/ingestion_orchestrator.py` after canonical stage, (2) keep all SQL in `app/db` by extending `SQLAlchemyCanonicalPersistenceService` for deterministic canonical-event reads and adding Task 7 write/query services in `app/db`, (3) follow existing API list envelope/validation pattern from `app/api/routers/ingestion.py` for snapshot read APIs, and (4) reuse existing UTC/local parsing conventions from `app/domain/flex_parsing.py` and report-date fields from canonical events. Reference review confirms useful conceptual patterns from `references/ngv_reports_ibkr`/`references/ibflex2` (open-position lot semantics and FIFO-related fields) without runtime code reuse.

2. **Add failing regression tests for FIFO and snapshot determinism** — `status: done`
	- **Description:** Create targeted tests that reproduce the required Task 7 outcomes before implementation.
	- [ ] Add tests that fail on current code for FIFO partial close behavior and realized/unrealized PnL expectations.
	- [ ] Add tests that fail on current code for deterministic daily snapshot output in `pnl_snapshot_daily`, including valuation/fx source fields.
	- [ ] Add date-boundary tests for UTC storage with Asia/Jerusalem report-date semantics, including DST edge coverage.
	- **Summary:** Added targeted regression coverage in `tests/test_ledger_fifo_snapshot.py` for (1) FIFO partial-close realized/unrealized PnL with fees, (2) deterministic outputs under timestamp tie ordering, and (3) Asia/Jerusalem report-date conversion across DST-edge UTC instants plus naive-timestamp validation. Verified red baseline by running `pytest tests/test_ledger_fifo_snapshot.py -q`, which currently fails at collection with `ModuleNotFoundError: app.ledger.fifo_engine` (expected prior to Task 7 implementation).

3. **Implement DB repository support for ledger inputs and outputs** — `status: done`
	- **Description:** Add/extend database-layer contracts and SQLAlchemy implementations required for Task 7 reads/writes while keeping all SQL inside `app/db`.
	- [ ] Extend `app/db/interfaces.py` with typed requests/records for position lot and daily snapshot persistence/query operations.
	- [ ] Implement deterministic canonical-event read queries needed by FIFO processing (ordered trade/cashflow/fx inputs).
	- [ ] Implement UPSERT/batch persistence for `position_lot` and `pnl_snapshot_daily` with stable ordering and idempotent behavior.
	- **Summary:** Added Task 7 typed DB contracts in `app/db/interfaces.py` (`LedgerTradeFillRecord`, `LedgerCashflowRecord`, `PositionLotUpsertRequest`, `PnlSnapshotDailyUpsertRequest`, `PnlSnapshotDailyRecord`, `LedgerSnapshotRepositoryPort`) and implemented `SQLAlchemyLedgerSnapshotService` in `app/db/ledger_snapshot.py`. Implementation includes deterministic canonical-event reads for FIFO inputs (`trade_timestamp_utc`, `source_raw_record_id` ordering), batch upsert for `position_lot`, batch upsert for `pnl_snapshot_daily` using the frozen unique constraint, and deterministic snapshot list queries with validated sort/date filters. Export wiring was added in `app/db/__init__.py`.

4. **Implement stocks-first FIFO ledger computation service** — `status: done`
	- **Description:** Build project-native FIFO lot matching and PnL computation service reusing existing contracts and decimal handling patterns.
	- [ ] Add ledger-domain data models and service implementation under `app/ledger/` for lot open/close lifecycle and per-instrument totals.
	- [ ] Ensure realized and unrealized PnL computations include fees/withholding impact per Task 7 outcome.
	- [ ] Integrate deterministic tie-breaking/order policy for canonical events to guarantee replay-stable FIFO results.
	- **Summary:** Implemented project-native FIFO engine in `app/ledger/fifo_engine.py` with typed Task 7 contracts (`FifoTradeFillInput`, `FifoLedgerComputationRequest`, `FifoLedgerComputationResult`) and deterministic computation function `fifo_compute_instrument`. Logic now enforces stable ordering by `trade_timestamp_utc` then `source_raw_record_id`, applies FIFO lot matching, and includes per-trade fees/withholding impacts in realized/unrealized PnL. Export wiring was added in `app/ledger/__init__.py`.

5. **Implement daily snapshot assembly and persistence flow** — `status: done`
	- **Description:** Produce and persist day-level snapshots from FIFO outputs and valuation/fx sources.
	- [ ] Add snapshot builder logic that assembles `position_qty`, `cost_basis`, `realized_pnl`, `unrealized_pnl`, `total_pnl`, fees, withholding, provisional flags, and source labels.
	- [ ] Enforce UTC persistence plus Asia/Jerusalem business-date boundary conversion for report date generation.
	- [ ] Persist snapshot rows through db-layer interfaces only and verify deterministic idempotent reruns.
	- **Summary:** Implemented snapshot date boundary and assembly flow with persistence. Added `app/ledger/snapshot_dates.py` (`snapshot_resolve_report_date_local`) for UTC -> Asia/Jerusalem business-date conversion (offset-aware validation), and `app/ledger/snapshot_service.py` (`StockLedgerSnapshotService`) to build day-level snapshot rows from canonical trade/cashflow inputs, compute per-instrument realized/unrealized/total PnL and impact fields, and persist via db-layer only (`db_position_lot_upsert_many`, `db_pnl_snapshot_daily_upsert_many`). Extended FIFO outputs to include open-lot persistence details and updated exports in `app/ledger/__init__.py`.

6. **Wire automatic snapshot execution and expose read APIs** — `status: done`
	- **Description:** Integrate Task 7 into existing runtime paths so snapshots are computed automatically after successful ingestion and are queryable via API.
	- [ ] Wire ingestion success path to trigger snapshot computation automatically with deterministic execution and failure signaling.
	- [ ] Add snapshot read API endpoints and repository-backed query handlers using existing API list/validation patterns.
	- [ ] Ensure diagnostics and error signaling follow existing ingestion/reprocess conventions without duplicate execution paths.
	- **Summary:** Automatic snapshot execution is now integrated into successful ingestion flow: `app/jobs/ingestion_orchestrator.py` appends a `snapshot` stage, executes Task 7 snapshot build/persist via `StockLedgerSnapshotService`, and persists snapshot diagnostics in run timeline. Runtime wiring was added in `app/bootstrap.py` using `SQLAlchemyLedgerSnapshotService`. Snapshot read APIs are now exposed at `GET /snapshots/daily` via new router `app/api/routers/snapshot.py` and included through `app/api/application.py` and router exports. API behavior follows existing list contract patterns (allowed sort fields, sort direction validation, `applied_limit`, filters envelope).

7. **Run full verification and lint gates for Task 7 changes** — `status: done`
	- **Description:** Validate Task 7 behavior and quality gates before documentation updates.
	- [ ] Run targeted Task 7 tests first, then broader project tests relevant to touched modules.
	- [ ] Run `ruff` and `pylint` according to `linting.md` and resolve all new issues in changed code.
	- [ ] Confirm no runtime imports from `references/` and no SQL outside `app/db`.
	- **Summary:** Validation and lint gates completed successfully: (1) targeted Task 7 tests pass, including new regressions (`tests/test_ledger_fifo_snapshot.py`, `tests/test_api_snapshot.py`, and ingestion orchestrator snapshot-stage coverage), (2) broader suite passes (`pytest -q` => 83 passed, 5 skipped), (3) `ruff check app/ --ignore=E501,W293,W291` passes, and (4) `pylint app/ --disable=C0303,R0913,R0914,R0917,C0301,R0911,R0912,C0302,C0305,R0902` passes with 10.00/10.

8. **Update README and memory** — `status: done`
	- **Description:** revise `README.md` and `ai_memory.md` to reflect changes
	- [ ] Update `README.md` with Task 7 capabilities, run paths, and verification notes.
	- [ ] Add durable implementation decisions/patterns/fixes to `ai_memory.md` using required date-tag format.
	- [ ] Remove or replace obsolete notes that conflict with implemented Task 7 behavior.
	- **Summary:** Updated `README.md` with a dedicated Task 7 section documenting FIFO ledger behavior, automatic ingestion-success snapshot stage, new `GET /snapshots/daily` API contract, and implementation modules. Updated `ai_memory.md` with durable Task 7 decisions/patterns (automatic snapshot execution, db-layer ledger snapshot boundary, snapshot API exposure policy, and FIFO deterministic ordering + open-lot output pattern).

## Clarifying Questions
Q1: Should Task 7 snapshot computation run automatically at the end of successful ingestion/reprocess runs, or only via a dedicated manual trigger (CLI/API)?
A1: automatically at the end of successful ingestion

Q2: For Task 7 scope, should we expose snapshot read APIs now, or keep outputs internal until Task 10 reporting endpoints?
A2: expose snapshot read APIs now
