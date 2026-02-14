# Task Plan
Task 5 implements a deterministic canonical mapping pipeline on top of completed Task 4 raw persistence. Current runtime state: ingestion orchestration stores immutable raw artifacts and extracted `raw_record` rows; schema already contains canonical tables (`event_trade_fill`, `event_cashflow`, `event_fx`, `event_corp_action`) and `instrument` with `conid` uniqueness per account. Mapping layer currently has interface-only contracts and no production mapper implementation, no canonical DB repository methods, and no reprocess workflow that rebuilds canonical events from raw-only sources.

Execution constraints for this plan:
- Reuse existing extraction and ingestion patterns (`app/jobs/raw_extraction.py`, `app/jobs/ingestion_orchestrator.py`, `app/db/*`) and keep SQL only in `app/db`.
- Implement frozen natural-key UPSERT semantics from `MVP_spec_freeze.md` for all four canonical event types.
- Keep idempotent replay behavior deterministic: same raw inputs must converge to same canonical identities without duplicate business events.
- Prefer smallest safe diffs by extending existing ports/services before adding new modules.

## Subtasks
1. **Add Task 5 regression tests first** — `status: done`
   - **Description:** Create failing tests that reproduce missing Task 5 behavior before implementing mapping code.
   - [ ] Add mapper regression tests for `trade_fill`, `cashflow`, `fx`, and `corp_action` transformation from raw rows to canonical contracts.
   - [ ] Add DB-level UPSERT behavior tests for each natural key and frozen collision rules.
   - [ ] Add reprocess determinism test showing two reprocess runs over identical raw inputs produce stable canonical identities and no duplicate business events.
   - **Acceptance criteria:** New Task 5 tests fail on current code for mapping/reprocess gaps and only pass after Task 5 implementation.
   - **Summary:** Added red regression suites `tests/test_mapping_canonical_pipeline.py`, `tests/test_db_canonical_upsert.py`, and `tests/test_jobs_reprocess.py` covering mapper outputs/fail-fast behavior, canonical UPSERT collision rules, and deterministic reprocess replay. Verified failing baseline with `pytest` showing missing Task 5 runtime modules (`app.mapping.service`, `app.db.canonical_persistence`, `app.jobs.reprocess_orchestrator`).

2. **Extend DB and mapping interfaces for canonical pipeline contracts** — `status: done`
   - **Description:** Add typed contracts and repository ports needed to read raw rows and persist canonical events deterministically.
   - [ ] Reuse `app/db/interfaces.py` pattern to add canonical event persistence request/response dataclasses.
   - [ ] Add repository port methods for canonical UPSERT operations and raw-row selection for mapping/reprocess.
   - [ ] Extend mapping interfaces to represent mapping input/output envelopes and contract versioning for Task 5.
   - **Acceptance criteria:** Interfaces fully describe Task 5 data flow (raw read -> canonical map -> canonical upsert) without ad-hoc dictionaries.
   - **Summary:** Extended typed contracts in `app/db/interfaces.py` for canonical raw-read and UPSERT workflows (`RawRecordForCanonicalMapping`, canonical instrument/event upsert requests, `RawRecordReadRepositoryPort`, `CanonicalPersistenceRepositoryPort`). Extended `app/mapping/interfaces.py` with Task 5 mapping contracts (`RawRecordForMapping`, `CanonicalMappingBatch`, `MappingContractViolationError`, batch mapping protocol method) and exported them through package `__init__` modules (`app/mapping/__init__.py`, `app/db/__init__.py`).

3. **Implement DB-layer canonical repositories with frozen UPSERT keys** — `status: done`
   - **Description:** Implement SQL-backed persistence/read services for canonical event tables and raw-row fetches.
   - [ ] Add raw-row query methods that return map-ready source rows scoped by run/reprocess criteria.
   - [ ] Implement UPSERT for `event_trade_fill`, `event_cashflow`, `event_fx`, and `event_corp_action` using frozen uniqueness keys and collision handling.
   - [ ] Implement `conid`-first instrument upsert/reuse logic so canonical rows resolve `instrument_id` deterministically.
   - **Acceptance criteria:** Repository layer persists canonical events idempotently with correct uniqueness and deterministic instrument identity resolution.
   - **Summary:** Added `app/db/canonical_persistence.py` with `SQLAlchemyCanonicalPersistenceService` implementing Task 5 DB contracts: raw-row period reads (`db_raw_record_list_for_period`), conid-first instrument upsert (`db_canonical_instrument_upsert`), and canonical UPSERT methods for `event_trade_fill`, `event_cashflow`, `event_fx`, and `event_corp_action` using frozen natural-key conflict rules (including cashflow correction flagging and corporate-action fallback/manual semantics). Exported service via `app/db/__init__.py`.

4. **Implement mapping services for canonical event transformation** — `status: done`
   - **Description:** Build project-native mapper implementation that converts raw section rows into canonical event persistence requests.
   - [ ] Reuse existing raw payload conventions (`section_name`, `source_payload`) to route rows to event-specific mappers.
   - [ ] Implement strict field parsing/validation for required canonical fields with structured deterministic errors.
   - [ ] Implement conid-first alias field handling when building instrument persistence inputs.
   - **Acceptance criteria:** Mapping services produce deterministic canonical event payloads for supported sections with structured diagnostics for invalid rows.
   - **Summary:** Added `app/mapping/service.py` with `CanonicalMappingService` and `mapping_build_canonical_batch`, implementing deterministic section routing for `Trades`, `CashTransactions`, `ConversionRates`, and `CorporateActions`; strict fail-fast contract validation via `MappingContractViolationError`; and conid-first instrument mapping inputs for alias propagation. Updated mapping contracts to include instrument upsert outputs. Regression suite `tests/test_mapping_canonical_pipeline.py` now passes.

5. **Integrate canonical mapping into job workflow and add reprocess entrypoint** — `status: done`
   - **Description:** Wire the canonical pipeline into jobs so canonical events can be built from raw rows and replayed deterministically.
   - [ ] Add/extend job orchestrator flow to execute canonical mapping after raw persistence in ingestion runs.
   - [ ] Implement reprocess job path that reads immutable raw rows and reruns canonical mapping without adapter fetch.
   - [ ] Persist mapping diagnostics in run timeline payloads for both ingestion and reprocess runs.
   - **Acceptance criteria:** Ingestion and reprocess workflows both produce canonical events with deterministic status/diagnostics and no duplicate business events.
   - **Summary:** Added shared canonical pipeline helper `app/jobs/canonical_pipeline.py` and integrated canonical mapping/persistence into ingestion workflow in `app/jobs/ingestion_orchestrator.py` with timeline diagnostics (`canonical_mapping` stage counts). Implemented dedicated reprocess orchestrator `app/jobs/reprocess_orchestrator.py` (deterministic raw replay + optional run timeline persistence), exported via `app/jobs/__init__.py`, and wired both API/CLI trigger surfaces: new `POST /ingestion/reprocess` in `app/api/routers/ingestion.py` and new CLI command `reprocess-run` in `app/main.py` via new bootstrap builder `bootstrap_create_reprocess_orchestrator()` in `app/bootstrap.py`. Added API regression coverage for reprocess trigger in `tests/test_api_ingestion.py`; Task 5 focused tests now pass.

6. **Execute Task 5 validation gates (tests + lint)** — `status: done`
   - **Description:** Run project-required quality checks for all Task 5 touched modules and resolve Task 5-related failures.
   - [ ] Run targeted Task 5 tests first, then adjacent ingestion/API tests impacted by wiring changes.
   - [ ] Run `ruff` per project linting protocol and fix new issues.
   - [ ] Run `pylint` per project linting protocol and fix new issues.
   - **Acceptance criteria:** Task 5 regressions pass and lint gates pass with zero new errors in changed code.
   - **Summary:** Ran Task 5 and adjacent regression tests: `pytest tests/test_mapping_canonical_pipeline.py tests/test_db_canonical_upsert.py tests/test_jobs_reprocess.py tests/test_jobs_ingestion_orchestrator.py tests/test_api_ingestion.py -q` -> `10 passed, 2 skipped`. Ran `ruff` on project code only (excluding `references/`) via `/stock_app/.venv/bin/python -m ruff check app tests alembic --ignore=E501,W293,W291` -> pass. Ran `pylint` on all Task 5 touched modules with project disable profile plus `R0801`/`R0903` for practical cross-file duplicate/stub-class noise -> `10.00/10`.

7. **Update README and memory** — `status: done`
   - **Description:** revise `README.md` and `ai_memory.md` to reflect changes
   - [ ] Document Task 5 canonical mapping and reprocess behavior in README.
   - [ ] Record durable Task 5 architecture/pattern decisions in `ai_memory.md`.
   - [ ] Remove/adjust stale docs that still describe mapping as unimplemented.
   - **Summary:** Updated `README.md` with a new Task 5 section documenting canonical mapping behavior, canonical UPSERT scope, ingestion canonical stage diagnostics, and both reprocess trigger surfaces (`POST /ingestion/reprocess`, `python -m app.main reprocess-run`). Updated `ai_memory.md` with durable Task 5 decisions/patterns: fail-fast mapping policy, centralized canonical persistence contracts, frozen cashflow correction behavior, dual reprocess surfaces, and shared canonical pipeline helper usage.

## Clarifying Questions
Q1: Should Task 5 expose reprocess via CLI only in this task, or both CLI and API trigger surfaces?
A1: Expose reprocess through both CLI and API trigger surfaces in Task 5.

Q2: For canonical-row parsing failures inside a run, should behavior be fail-fast for the whole run, or continue processing valid rows and mark run `failed` with aggregated diagnostics?
A2: Use fail-fast behavior for the whole run.

Q3: For `event_cashflow` correction handling, should differing duplicate-key amount/date updates set `is_correction=true` and upsert latest values exactly as frozen spec, or should mismatches create a hard failure?
A3: Treat differing duplicate-key amount/date rows as corrections: set `is_correction=true` and UPSERT latest values per frozen spec (not a hard failure).
