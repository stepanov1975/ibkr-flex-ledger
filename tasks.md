# Task Plan
Task 1 implementation scope: build the runtime foundation and module boundaries for the MVP as defined in `implementation_task_list.md` and `MVP.md` Milestone 0. Current codebase has no project runtime modules outside `references/`, so this task starts from greenfield scaffolding. Work will follow project rules: reference patterns are consulted from `references/` but not imported, database access is centralized in db modules, and delivery is sequential milestone-by-milestone with each subtask moved from `planned` -> `in progress` -> `done` before starting the next. If interrupted, resume by opening this file, selecting the first `planned` subtask, setting it to `in progress`, and executing only that milestone.

## Subtasks
1. **Create runtime skeleton with stable module boundaries** — `status: done`
   - **Description:** Create the initial project-native package structure and entry-point layout for adapters, mapping, ledger, analytics, API/UI, jobs, and db layers so future modules can be added without rewrites.
   - [x] Create top-level runtime package and subpackages for each required layer.
   - [x] Use FastAPI as the API/UI framework for foundation module structure and entrypoint wiring.
   - [x] Add module-level docstrings and typed public interfaces for each layer boundary.
   - [x] Add architecture conventions file that states db-only data access rule and reference-code boundary rule.
   - **Summary:** Created modular runtime skeleton under `app/` for adapters, mapping, ledger, analytics, API, jobs, db, and domain models; added FastAPI foundation app factory and typed layer interfaces; added architecture boundary rules in `docs/architecture_conventions.md`; validated with `./.venv/bin/ruff check app --ignore=E501,W293,W291` and `./.venv/bin/pylint app --disable=C0303,R0913,R0914,R0917,C0301,R0911,R0912,C0302,C0305`.

2. **Add configuration model and startup wiring** — `status: done`
   - **Description:** Implement a validated configuration model for Flex credentials and runtime settings, and wire app startup so runtime fails fast on invalid configuration.
   - [x] Implement typed settings model for environment variables (Flex token/query id, DB URL, runtime defaults).
   - [x] Implement `.env` support for local development plus environment-variable overrides for deployment.
   - [x] Implement app bootstrap entry point with `if __name__ == "__main__":` block and dependency initialization.
   - [x] Add startup validation behavior with explicit, actionable error reporting for missing/invalid required settings.
   - **Summary:** Added `app/config/settings.py` with `AppSettings` (`pydantic-settings`) and `.env` loading plus environment overrides, startup validation with `SettingsLoadError`, and bootstrap/runtime entrypoints in `app/bootstrap.py` and `app/main.py`; updated API factory to use validated settings metadata.

3. **Implement db layer baseline and connectivity health service** — `status: done`
   - **Description:** Add db module baseline (connection/session management and repository boundary) and a health endpoint/service that verifies application and database connectivity.
   - [x] Implement db connection factory in db layer only and expose narrow interface for non-db modules.
   - [x] Implement health check logic that verifies app liveness and database connectivity.
   - [x] Expose health endpoint through API layer and return deterministic success/failure payload.
   - [x] Add targeted tests covering healthy DB response and DB-unavailable failure behavior.
   - **Summary:** Added `app/db/session.py` and `app/db/health.py` with SQLAlchemy connection factory and db connectivity check service, added `/health` API router in `app/api/routers/health.py`, wired dependencies through `app/bootstrap.py` and API factory, and added tests in `tests/test_api_health.py` (plus `tests/conftest.py` for import path). Validation: `pytest tests/test_api_health.py` passed and lint checks passed for `app` and `tests`.

4. **Containerize runtime foundation with one-command boot** — `status: done`
   - **Description:** Add Docker Compose and runtime container configuration so app + PostgreSQL boot together and satisfy Task 1 one-command startup requirement.
   - [x] Add Dockerfile and compose configuration for app and PostgreSQL services.
   - [x] Wire startup order and connection configuration so app can connect to PostgreSQL in compose network.
   - [x] Validate one-command boot flow and document exact command and expected healthy state.
   - [x] Ensure no SQL/ORM calls exist outside db modules after wiring.
   - **Summary:** Added `Dockerfile`, `docker-compose.yml`, and `.dockerignore`; wired app startup via `python -m app.main` with `depends_on: service_healthy` and environment configuration for DB/app/Flex placeholders. Resolved local port collision by mapping PostgreSQL host port to `5433`. Validation: `docker compose up -d` succeeds, `docker compose ps` shows healthy services, and `curl http://127.0.0.1:8000/health` returns `status=ok`.

5. **Enforce foundation quality gate for Task 1** — `status: done`
   - **Description:** Run required quality checks and confirm Task 1 acceptance criteria before moving to Task 2.
   - [x] Run targeted tests added for foundation behavior and confirm they pass.
   - [x] Run `ruff` and `pylint` per project linting protocol with zero new errors.
   - [x] Verify Task 1 acceptance criteria: one-command boot, DB health success, db-layer-only data access.
   - [x] Record files changed and verification evidence in this task file summary.
   - **Summary:** Verification evidence: `pytest tests/test_api_health.py` -> 2 passed; `./.venv/bin/ruff check app tests --ignore=E501,W293,W291` -> passed; `./.venv/bin/pylint app tests --disable=C0303,R0913,R0914,R0917,C0301,R0911,R0912,C0302,C0305` -> 10.00/10. Runtime acceptance checks: `docker compose up -d` starts app+postgres, `/health` returns success with DB check, and SQL usage remains confined to `app/db/*` (validated by code search).

6. **Update README and memory** — `status: done`
   - **Description:** revise `README.md` and `ai_memory.md` to reflect changes
   - [x] Update `README.md` with foundation runtime setup and health-check usage.
   - [x] Update `ai_memory.md` with durable architectural decisions from Task 1.
   - [x] Remove/replace any stale notes that conflict with implemented runtime structure.
   - **Summary:** Added Task 1 runtime section to `README.md` (compose startup, health endpoint, config loading and required settings) and recorded durable architecture decisions/patterns in `ai_memory.md` for FastAPI module boundaries, startup validation behavior, db health delegation pattern, and compose port policy.

## Clarifying Questions
Q1: Which web framework should be used for Task 1 foundation (`FastAPI` default proposal, or another framework)?
A1: FastAPI.

Q2: For configuration loading, should implementation prioritize `.env` support for local dev plus environment variables for deployment (default), or environment variables only?
A2: `.env` support for local dev plus environment variables for deployment.
