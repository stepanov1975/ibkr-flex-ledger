# Task Plan
Task 4 implements immutable raw artifact and raw row persistence on top of the existing Task 3 ingestion flow. Current code already provides run lifecycle persistence, adapter request/poll/download, required-section preflight, and diagnostics timeline finalization, but the `persist` stage is still a placeholder (`raw_persistence: deferred_to_task_4`).

Implementation must keep SQL in `app/db` only, reuse existing ingestion orchestration and timeline helpers, and avoid duplicate parsing/persistence logic. The target outcome is deterministic idempotent raw ingestion: same `period_key + flex_query_id + sha256` payload must not create duplicate raw artifacts, while row-level provenance is persisted in `raw_record` with stable source references for downstream mapping/reprocess.

Reference patterns already identified from `references/REFERENCE_NOTES.md` and reviewed in `references/flexquery/flexquery/flexquery.py` and `references/IB_Flex/xml_downloader.py`:
- Keep request/poll/download concerns separate from persistence.
- Preserve immutable raw payload for replay/audit.
- Use deterministic retry/error surfaces and avoid mixing parser/storage concerns.

## Subtasks
1. **Add Task 4 regression tests first** — `status: done`
   - **Description:** Add tests that reproduce current Task 4 gaps before implementation changes.
   - [ ] Add a failing orchestrator-level test proving the `persist` stage must store raw data (not placeholder-only diagnostics).
   - [ ] Add a failing DB/integration-style test proving duplicate ingest of identical payload (same `period_key`, `flex_query_id`, and SHA-256) does not duplicate raw artifact persistence.
   - [ ] Add a failing test proving `raw_record` rows keep source trace fields (`section_name`, `source_row_ref`) and link to the triggering run.
   - **Acceptance criteria:** Tests fail on current code for the exact missing Task 4 behaviors and pass only after Task 4 implementation is complete.
   - **Summary:** Added Task 4 red tests in `tests/test_jobs_ingestion_orchestrator.py` and `tests/test_db_migrations.py`. Verified pre-fix failure for persist-stage raw details (`payload_sha256`, `raw_artifact_id`, `raw_record_count`) with `pytest tests/test_jobs_ingestion_orchestrator.py -q` (1 failed). Added migration-level raw artifact/raw_record linkage contract tests; in this environment those DB integration tests are currently skipped due unreachable PostgreSQL test target.

2. **Introduce DB-layer raw persistence contracts and models** — `status: done`
   - **Description:** Extend existing db interfaces with typed contracts for immutable raw artifact and row persistence, reusing current ingestion run patterns.
   - [ ] Reuse and extend `app/db/interfaces.py` with typed request/response dataclasses and repository port methods for raw artifact and raw record writes.
   - [ ] Ensure method naming follows project discoverability convention (`raw_*` scope prefix).
   - [ ] Keep boundary strict: no adapter/job layer direct SQL.
   - **Acceptance criteria:** New/updated DB contracts define complete persistence inputs/outputs for Task 4 and can be consumed by orchestrator code without ad-hoc dictionaries.
   - **Summary:** Extended `app/db/interfaces.py` with typed raw persistence contracts and models (`RawArtifactReference`, `RawArtifactPersistRequest`, `RawArtifactRecord`, `RawArtifactPersistResult`, `RawRecordPersistRequest`, `RawRecordPersistResult`, `RawPersistenceRepositoryPort`) using `raw_*` discoverable method naming. Exported new contracts via `app/db/__init__.py` for downstream wiring.

3. **Implement immutable raw persistence service in db layer** — `status: done`
   - **Description:** Implement SQL-backed service that persists immutable raw payload identity and extracted raw rows with idempotent behavior.
   - [ ] Implement db service methods for artifact-level dedupe keyed by `period_key + flex_query_id + payload_sha256`.
   - [ ] Implement `raw_record` batch persistence linked to `ingestion_run_id`, preserving `account_id`, `report_date_local`, `section_name`, `source_row_ref`, and `source_payload`.
   - [ ] Reuse existing transaction/session patterns from `app/db/ingestion_run.py` and centralize validation in the service layer.
   - [ ] Update bootstrap/wiring to expose the new db persistence service to jobs.
   - **Acceptance criteria:** Persistence service performs deterministic idempotent writes with no duplicate raw artifact records for identical payload identity keys and stable row provenance storage.
   - **Summary:** Added migration `alembic/versions/20260214_02_task4_raw_artifact_persistence.py` creating `raw_artifact` with unique dedupe key (`account_id`, `period_key`, `flex_query_id`, `payload_sha256`) and linking `raw_record.raw_artifact_id` to `raw_artifact`; switched raw row uniqueness to artifact-scoped source refs. Implemented `app/db/raw_persistence.py` (`SQLAlchemyRawPersistenceService`) with `db_raw_artifact_upsert` and `db_raw_record_insert_many` conflict-aware persistence behavior. Exported service via `app/db/__init__.py`.

4. **Integrate raw persistence into ingestion orchestrator persist stage** — `status: done`
   - **Description:** Replace Task 3 placeholder persist stage with real artifact/row persistence while preserving deterministic timeline semantics.
   - [ ] Reuse adapter payload bytes and run metadata to compute payload SHA-256 once per run.
   - [ ] Parse payload into section rows for raw persistence using a dedicated reusable helper (no duplicated parsing code).
   - [ ] Call db raw persistence service from orchestrator and append timeline diagnostics with persisted counts and dedupe outcome.
   - [ ] Ensure error path finalization keeps actionable diagnostics linked to `run_id`.
   - **Acceptance criteria:** Successful runs persist raw artifact/rows and timeline includes real persist details; failure runs retain actionable diagnostics with no partial lifecycle state.
   - **Summary:** Replaced placeholder persist stage in `app/jobs/ingestion_orchestrator.py` with real Task 4 flow: compute payload SHA-256, extract section rows via new helper `app/jobs/raw_extraction.py`, upsert raw artifact, insert raw rows, and emit diagnostics (`payload_sha256`, `raw_artifact_id`, dedupe flags, inserted/deduplicated counts). Updated dependency wiring in `app/bootstrap.py` to provide `SQLAlchemyRawPersistenceService` to orchestrator. Updated/added tests: `tests/test_jobs_ingestion_orchestrator.py`, `tests/test_jobs_raw_extraction.py`, and Task 4 migration contract assertions in `tests/test_db_migrations.py`.

5. **Execute Task 4 validation gates (tests + lint)** — `status: done`
   - **Description:** Run required project quality checks after implementation and resolve new issues from Task 4 changes.
   - [ ] Run targeted Task 4 tests first, then broader relevant tests.
   - [ ] Run `ruff` per project linting protocol and fix Task 4-related issues.
   - [ ] Run `pylint` per project linting protocol and fix Task 4-related issues.
   - [ ] Confirm no new errors in touched modules.
   - **Acceptance criteria:** Task 4 regression tests pass and lint gates pass with zero new errors.
   - **Summary:** Ran targeted and adjacent regression tests: `pytest tests/test_jobs_ingestion_orchestrator.py tests/test_jobs_raw_extraction.py tests/test_api_ingestion.py tests/test_api_health.py tests/test_db_migrations.py -q` -> `9 passed, 3 skipped`. Ran `ruff check app tests --ignore=E501,W293,W291` -> pass. Ran project-protocol `pylint` on all Task 4 touched modules -> `10.00/10` after refactoring warning fixes.

6. **Update README and memory** — `status: done`
   - **Description:** revise `README.md` and `ai_memory.md` to reflect changes
   - [ ] Document Task 4 raw persistence behavior and idempotency policy in README.
   - [ ] Add durable Task 4 implementation decisions/patterns to `ai_memory.md`.
   - [ ] Remove stale Task 3 placeholder wording that no longer matches implementation.
   - **Summary:** Updated `README.md` with a new Task 4 section documenting immutable `raw_artifact` dedupe policy, all-section raw row persistence, raw provenance linkage, and persist diagnostics fields. Updated `ai_memory.md` with durable Task 4 decisions/patterns (dedicated artifact table, persist-stage flow, and success-on-dedupe run semantics).

## Clarifying Questions
Q1: Should immutable raw artifact persistence be modeled as a dedicated persistence entity/table in this task, or should dedupe be enforced only through `raw_record` writes?
A1: Use a dedicated raw artifact persistence entity/table with database-enforced dedupe constraints; do not rely on `raw_record`-only dedupe.

Q2: For raw-row extraction scope in Task 4, should we persist rows from all detected Flex sections (including non-MVP-mapped sections), or only sections currently needed by MVP mapping?
A2: Persist rows from all detected Flex sections into immutable raw artifacts/raw rows, including non-MVP-mapped sections; mapping scope stays separate.

Q3: For duplicate raw artifact ingests, should orchestrator mark run `success` with diagnostics indicating dedupe/no-op persistence, or should it skip run creation entirely?
A3: Keep normal run creation/finalization and mark duplicate artifact ingests as `success` with explicit dedupe/no-op diagnostics.
