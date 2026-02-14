# Task Plan
Task 3 implements MVP ingestion orchestration on top of the current foundation-only codebase. The application currently has health-only API routing and interface stubs for adapters/jobs/db, with Task 2 schema already providing `ingestion_run` and `raw_record`. Implementation should extend existing layer boundaries (no cross-layer SQL), reuse the current protocol-driven style, and follow frozen contracts from `MVP.md` and `MVP_spec_freeze.md`: deterministic `request -> poll -> download -> persist` stages, single active run lock (`409 run already active`), required Flex section preflight with `MISSING_REQUIRED_SECTION`, and visible run stage timeline/status transitions (`started/success/failed`). Reference patterns were reviewed from `references/flexquery/flexquery/flexquery.py` and `references/IB_Flex/xml_downloader.py` for request/poll/retry flow, then adapted to project-native modules only.

## Subtasks
1. **Implement ingestion run persistence and lock boundary** — `status: done`
	- **Description:** Add db-layer contracts and implementation for creating/updating ingestion runs, enforcing single-active-run lock, and querying run timeline data using existing `ingestion_run` schema fields.
	- [ ] Add explicit runtime `ACCOUNT_ID` setting and thread it into ingestion run creation paths.
	- [ ] Add/extend typed interfaces for ingestion run repository operations in db-layer modules.
	- [ ] Implement transactional lock check + run creation path that guarantees only one `started` run at a time.
	- [ ] Implement run completion update path (`success`/`failed`, `ended_at_utc`, `duration_ms`, `error_code`, `error_message`, `diagnostics`).
	- [ ] Add run list/detail read methods needed by API acceptance criteria.
	- **Acceptance criteria:** concurrent trigger attempts cannot both create `started` runs; persisted rows contain deterministic lifecycle fields and are queryable by run id.
	- **Summary:** Added explicit `account_id` runtime setting in `app/config/settings.py`; introduced db-layer ingestion run lifecycle contracts and typed models in `app/db/interfaces.py`; implemented PostgreSQL advisory-lock-backed create/finalize/list/detail service in `app/db/ingestion_run.py`; exported new db services/contracts in `app/db/__init__.py`. Validated with `ruff`, `pylint`, and `pytest tests/test_api_health.py`.

2. **Implement Flex adapter request-poll-download workflow** — `status: done`
	- **Description:** Implement project-native adapter service behind `FlexAdapterPort` for upstream Flex retrieval, using deterministic retry/poll behavior and structured error reporting.
	- [ ] Implement adapter class in `app/adapters` that calls IB Flex endpoints in request then poll/download stages.
	- [ ] Add bounded retry/backoff and deterministic timeout handling for polling.
	- [ ] Normalize adapter output to immutable payload bytes plus upstream run reference.
	- [ ] Convert upstream failures into specific, actionable error types/messages for ingestion diagnostics.
	- **Acceptance criteria:** adapter returns payload bytes when report is ready; timeout and rejected requests produce deterministic, structured failures.
	- **Summary:** Added project-native Flex adapter implementation in `app/adapters/flex_web_service.py` with deterministic request->poll->download flow, bounded retry/backoff, upstream response parsing, and actionable error mapping (`ConnectionError`, `TimeoutError`, `ValueError`, `RuntimeError`); exported adapter in `app/adapters/__init__.py`. Validated with `ruff`, `pylint`, and regression `pytest tests/test_api_health.py`.

3. **Implement required-section preflight validator** — `status: done`
	- **Description:** Add section-matrix validation against frozen policy before downstream publish, with explicit missing-section diagnostics.
	- [ ] Add reusable constants/config for hard-required and reconciliation-required section names from frozen spec.
	- [ ] Parse downloaded payload metadata to detect available section names.
	- [ ] Implement preflight validator returning explicit missing-section sets by policy group.
	- [ ] Wire validation failures to deterministic `MISSING_REQUIRED_SECTION` diagnostics payload.
	- **Acceptance criteria:** missing hard-required section fails run with `MISSING_REQUIRED_SECTION` and exact section names.
	- **Summary:** Added frozen section-matrix constants and reusable preflight validator in `app/jobs/section_preflight.py`, including XML section extraction, required/reconciliation missing-section detection, deterministic `MISSING_REQUIRED_SECTION` error handling, and JSON-array diagnostics builder; exported utilities in `app/jobs/__init__.py`.

4. **Implement ingestion orchestration service and stage timeline** — `status: done`
	- **Description:** Implement job-layer orchestration that composes lock, adapter fetch, section preflight, and run lifecycle transitions with stage-level diagnostics.
	- [ ] Implement a concrete orchestrator in `app/jobs` using existing `JobOrchestratorPort` and db/adapters interfaces.
	- [ ] Execute deterministic stage sequence (`request`, `poll`, `download`, `persist`) with stage timestamps.
	- [ ] Persist stage timeline and failure context in `ingestion_run.diagnostics` as a structured JSON array.
	- [ ] Ensure all failures end run in `failed` state and all success paths end run in `success` state.
	- **Acceptance criteria:** each run has a deterministic stage timeline as a structured JSON array plus terminal status; no partial lifecycle states remain after completion/failure.
	- **Summary:** Added concrete ingestion orchestrator in `app/jobs/ingestion_orchestrator.py` using db/adapters ports, deterministic stage execution with persisted diagnostics JSON-array timeline, and terminal-state finalization (`success`/`failed`) on every execution path; extended adapter result timeline support and extracted shared stage-event helper in `app/domain/timeline.py` for DRY consistency.

5. **Expose ingestion run APIs and CLI trigger surfaces** — `status: done`
	- **Description:** Add ingestion API and CLI trigger entrypoints plus dependency wiring so users can start runs and inspect run list/detail timeline with frozen error behavior.
	- [ ] Add ingestion router endpoints for trigger, run list, and run detail aligned with MVP minimum API surface.
	- [ ] Add CLI entrypoint to trigger ingestion runs through the same job/orchestrator path as API.
	- [ ] Return `409` with message `run already active` when lock rejects overlapping trigger.
	- [ ] Return run detail payload including final status and stage timeline diagnostics.
	- [ ] Wire API and CLI dependency composition through bootstrap/application entrypoint wiring.
	- **Acceptance criteria:** API and CLI triggers both enforce overlap policy; run detail endpoint shows stage timeline and final status for completed/failed runs.
	- **Summary:** Added ingestion API router (`POST /ingestion/run`, `GET /ingestion/runs`, `GET /ingestion/runs/{id}`) in `app/api/routers/ingestion.py`, wired it through `app/api/application.py` and `app/api/routers/__init__.py`, integrated db/adapters/jobs in `app/bootstrap.py`, and added CLI trigger command `ingestion-run` in `app/main.py`; overlap lock now maps to HTTP `409` with `run already active` message.

6. **Add regression tests and execute quality gates** — `status: done`
	- **Description:** Add tests that reproduce Task 3 required behaviors first, then implement/fix to pass, and run mandated lint/test commands.
	- [ ] Add failing tests for overlap lock (`409 run already active`) and missing required sections (`MISSING_REQUIRED_SECTION`).
	- [ ] Add tests verifying lifecycle transitions and run detail timeline shape.
	- [ ] Run targeted tests, then broader relevant test set.
	- [ ] Run `ruff check . --ignore=E501,W293,W291` and `pylint` per project linting protocol; resolve new issues.
	- **Acceptance criteria:** new regression tests pass; Task 3 acceptance criteria are covered by tests; lint gates pass with zero new errors.
	- **Summary:** Added regression tests `tests/test_api_ingestion.py`, `tests/test_jobs_section_preflight.py`, and `tests/test_jobs_ingestion_orchestrator.py` covering overlap lock (`409 run already active`), required-section diagnostics (`MISSING_REQUIRED_SECTION`), lifecycle success/failure transitions, and run detail diagnostics timeline shape; updated `tests/test_api_health.py` for new app dependencies. Quality gates passed: targeted+broader tests (`7 passed, 1 skipped` including migration regression), `ruff check app tests`, and `pylint` on all Task 3 touched runtime/test modules (10.00/10).

7. **Update README and memory** — `status: done`
	- **Description:** revise `README.md` and `ai_memory.md` to reflect changes
	- [ ] Document new ingestion endpoints, run lock behavior, and required-section diagnostics in README.
	- [ ] Add durable implementation decisions/patterns from Task 3 to `ai_memory.md`.
	- [ ] Remove or adjust any stale documentation that conflicts with implemented behavior.
	- **Summary:** Updated `README.md` with Task 3 ingestion orchestration baseline, API/CLI trigger surfaces, lock behavior (`409 run already active`), `MISSING_REQUIRED_SECTION` preflight semantics, diagnostics timeline persistence, and added `ACCOUNT_ID` to required settings. Updated `.env.example` to include `ACCOUNT_ID`. Added durable Task 3 decisions/patterns to `ai_memory.md`.

## Clarifying Questions
Q1: For Task 3 trigger surface, should implementation include both API trigger (`POST /ingestion/run`) and a CLI entrypoint now, or API trigger only in this task?
A1: include both API trigger (`POST /ingestion/run`) and a CLI entrypoint now

Q2: For single-account context in Task 3, should we introduce an explicit `ACCOUNT_ID` runtime setting now, or keep using an internal fixed value until Task 4/5 mapping is added?
A2: introduce an explicit `ACCOUNT_ID` runtime setting now

Q3: For run stage timeline payload, should diagnostics be persisted as a structured JSON array under `ingestion_run.diagnostics` in Task 3?
A3: yes
