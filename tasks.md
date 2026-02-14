# Task Plan
Task 1 implementation scope: build the runtime foundation and module boundaries for the MVP as defined in `implementation_task_list.md` and `MVP.md` Milestone 0. Current codebase has no project runtime modules outside `references/`, so this task starts from greenfield scaffolding. Work will follow project rules: reference patterns are consulted from `references/` but not imported, database access is centralized in db modules, and delivery is sequential milestone-by-milestone with each subtask moved from `planned` -> `in progress` -> `done` before starting the next. If interrupted, resume by opening this file, selecting the first `planned` subtask, setting it to `in progress`, and executing only that milestone.

## Subtasks
1. **Create runtime skeleton with stable module boundaries** — `status: planned`
   - **Description:** Create the initial project-native package structure and entry-point layout for adapters, mapping, ledger, analytics, API/UI, jobs, and db layers so future modules can be added without rewrites.
   - [ ] Create top-level runtime package and subpackages for each required layer.
   - [ ] Add module-level docstrings and typed public interfaces for each layer boundary.
   - [ ] Add architecture conventions file that states db-only data access rule and reference-code boundary rule.
   - [ ] Pending information from user: final web framework/module naming preference (if different from default selected during implementation).
   - **Summary:** (fill after completion)

2. **Add configuration model and startup wiring** — `status: planned`
   - **Description:** Implement a validated configuration model for Flex credentials and runtime settings, and wire app startup so runtime fails fast on invalid configuration.
   - [ ] Implement typed settings model for environment variables (Flex token/query id, DB URL, runtime defaults).
   - [ ] Implement app bootstrap entry point with `if __name__ == "__main__":` block and dependency initialization.
   - [ ] Add startup validation behavior with explicit, actionable error reporting for missing/invalid required settings.
   - [ ] Pending information from user: preferred secret source (.env only vs environment-only in deployment).
   - **Summary:** (fill after completion)

3. **Implement db layer baseline and connectivity health service** — `status: planned`
   - **Description:** Add db module baseline (connection/session management and repository boundary) and a health endpoint/service that verifies application and database connectivity.
   - [ ] Implement db connection factory in db layer only and expose narrow interface for non-db modules.
   - [ ] Implement health check logic that verifies app liveness and database connectivity.
   - [ ] Expose health endpoint through API layer and return deterministic success/failure payload.
   - [ ] Add targeted tests covering healthy DB response and DB-unavailable failure behavior.
   - **Summary:** (fill after completion)

4. **Containerize runtime foundation with one-command boot** — `status: planned`
   - **Description:** Add Docker Compose and runtime container configuration so app + PostgreSQL boot together and satisfy Task 1 one-command startup requirement.
   - [ ] Add Dockerfile and compose configuration for app and PostgreSQL services.
   - [ ] Wire startup order and connection configuration so app can connect to PostgreSQL in compose network.
   - [ ] Validate one-command boot flow and document exact command and expected healthy state.
   - [ ] Ensure no SQL/ORM calls exist outside db modules after wiring.
   - **Summary:** (fill after completion)

5. **Enforce foundation quality gate for Task 1** — `status: planned`
   - **Description:** Run required quality checks and confirm Task 1 acceptance criteria before moving to Task 2.
   - [ ] Run targeted tests added for foundation behavior and confirm they pass.
   - [ ] Run `ruff` and `pylint` per project linting protocol with zero new errors.
   - [ ] Verify Task 1 acceptance criteria: one-command boot, DB health success, db-layer-only data access.
   - [ ] Record files changed and verification evidence in this task file summary.
   - **Summary:** (fill after completion)

6. **Update README and memory** — `status: planned`
   - **Description:** revise `README.md` and `ai_memory.md` to reflect changes
   - [ ] Update `README.md` with foundation runtime setup and health-check usage.
   - [ ] Update `ai_memory.md` with durable architectural decisions from Task 1.
   - [ ] Remove/replace any stale notes that conflict with implemented runtime structure.
   - **Summary:** (fill after completion)

## Clarifying Questions
Q1: Which web framework should be used for Task 1 foundation (`FastAPI` default proposal, or another framework)?
A1: Pending information from user.

Q2: For configuration loading, should implementation prioritize `.env` support for local dev plus environment variables for deployment (default), or environment variables only?
A2: Pending information from user.
