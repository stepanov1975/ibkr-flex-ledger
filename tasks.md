# Task Plan
Implement Task 2 from `implementation_task_list.md`: deliver the MVP schema and migration baseline on top of the existing Task 1 foundation. The implementation will use project-native database-layer modules only, create reproducible migrations for all MVP tables, and enforce frozen natural-key/provenance constraints from `MVP_spec_freeze.md`. Work will proceed strictly one milestone at a time (`planned` -> `in progress` -> `done`) with each milestone ending in a verifiable done state before moving to the next. Existing code to reuse: `app/config/settings.py` for runtime DB configuration, `app/db/session.py` for SQLAlchemy engine/session patterns, and project testing/linting gates in `testing.md` and `linting.md`. Reference projects have been reviewed via `references/REFERENCE_NOTES.md` and direct inspection (`references/IB_Flex/importdata.py`) for persistence-table bootstrap patterns; implementation remains PostgreSQL + Alembic native in this repository.

Implementation decisions fixed from clarifications:
- Task 2 will implement a full column-level schema for all MVP tables now.
- UUID primary keys will be database-generated with PostgreSQL `gen_random_uuid()`.

## Subtasks
1. **Define Task 2 schema contract and migration scope** — `status: done`
	- **Description:** Consolidate the exact Task 2 table/constraint/index requirements from `implementation_task_list.md`, `MVP.md`, and `MVP_spec_freeze.md` into a single implementation contract to prevent drift before writing migrations.
	- [ ] Enumerate MVP tables required in Task 2 (`instrument`, `label`, `instrument_label`, `note`, `ingestion_run`, `raw_record`, `event_trade_fill`, `event_cashflow`, `event_fx`, `event_corp_action`, `position_lot`, `pnl_snapshot_daily`).
	- [ ] Enumerate frozen natural-key uniqueness constraints for canonical event tables.
	- [ ] Enumerate required provenance links (`ingestion_run_id`, source raw row linkage fields).
	- **Summary:** Completed. Created [docs/task2_schema_contract.md](docs/task2_schema_contract.md) with a full column-level schema contract for all 12 MVP tables, explicit frozen natural-key constraint names, and required provenance links (`ingestion_run_id`, `source_raw_record_id`). Contract also fixes global decisions for Task 2: DB-generated UUID PKs via `gen_random_uuid()` and UTC timestamp storage.

2. **Add Alembic migration scaffold integrated with app settings** — `status: done`
	- **Description:** Create migration infrastructure that reuses existing project configuration patterns, so schema changes are reproducible and environment-driven without duplicating DB configuration logic.
	- [ ] Create Alembic configuration and script directory in repository runtime code.
	- [ ] Wire Alembic environment to read DB URL through project settings/environment contract.
	- [ ] Add migration entry guidance/commands to project workflow docs where needed.
	- **Summary:** Completed. Added Alembic scaffold files [alembic.ini](alembic.ini), [alembic/env.py](alembic/env.py), [alembic/script.py.mako](alembic/script.py.mako), and [alembic/versions/.gitkeep](alembic/versions/.gitkeep). Added migration DB URL loader in [app/config/settings.py](app/config/settings.py) via `config_load_database_url()` and exported it from [app/config/__init__.py](app/config/__init__.py). Added workflow guidance in [docs/migrations.md](docs/migrations.md).

3. **Implement initial MVP schema migration with core tables** — `status: done`
	- **Description:** Create the baseline migration that introduces all Task 2 tables with primary keys, foreign keys, nullability, and data types aligned to MVP contracts.
	- [ ] Add one baseline migration that creates all Task 2 MVP tables.
	- [ ] Include relationship constraints (for labels, notes, events, lots, snapshots) consistent with single-account MVP policy.
	- [ ] Ensure canonical event tables include provenance columns and deterministic key columns required by frozen spec.
	- **Summary:** Completed. Added baseline migration [alembic/versions/20260214_01_task2_mvp_schema_baseline.py](alembic/versions/20260214_01_task2_mvp_schema_baseline.py) that creates all 12 MVP tables with full column-level definitions, PK/FK/nullability/data types, UTC timestamps, DB-generated UUID defaults (`gen_random_uuid()`), and provenance fields linking canonical events to both `ingestion_run` and `raw_record`.

4. **Add natural-key constraints and performance indexes** — `status: done`
	- **Description:** Finalize deterministic UPSERT identity and query performance essentials required for downstream ingestion/mapping/reports without adding post-MVP schema scope.
	- [ ] Add unique constraints matching frozen natural keys for `event_trade_fill`, `event_cashflow`, `event_fx`, and `event_corp_action`.
	- [ ] Add essential supporting indexes for ingestion lookup and provenance traversal.
	- [ ] Confirm constraint/index names match frozen naming expectations where specified.
	- **Summary:** Completed. Implemented frozen natural-key constraints in baseline migration with exact names: `uq_event_trade_fill_account_exec`, `uq_event_cashflow_account_txn_action_ccy`, `uq_event_fx_account_txn_ccy_pair`, `uq_event_corp_action_account_action` plus fallback `uq_event_corp_action_fallback`. Added essential indexes for ingestion lookup, report-date access, and provenance traversal across raw/canonical/snapshot tables.

5. **Validate migration idempotency and schema reproducibility** — `status: done`
	- **Description:** Prove Task 2 acceptance criteria by running migrations on a fresh database and re-running them safely, with regression tests for critical schema guarantees.
	- [ ] Add/extend tests that reproduce migration baseline expectations (fresh apply, no manual edits).
	- [ ] Verify migration re-run behavior is idempotent for the current head.
	- [ ] Record results and any non-task blockers discovered during validation.
	- **Summary:** Completed with targeted regression coverage in [tests/test_db_migrations.py](tests/test_db_migrations.py). Test provisions a temporary PostgreSQL database, runs `alembic upgrade head` twice to validate re-run idempotency semantics, and verifies baseline tables plus frozen natural-key constraints exist. In this environment, the migration test safely skips when no reachable PostgreSQL credentials are available; targeted test run result: `2 passed, 1 skipped` (including existing health tests).

6. **Run linting and quality gates for Task 2 changes** — `status: done`
	- **Description:** Execute required project quality checks for all newly added/modified Task 2 files before handoff.
	- [ ] Run project Python linting commands required by `linting.md` (`ruff`, `pylint`).
	- [ ] Resolve all new lint errors introduced by Task 2 changes.
	- [ ] Re-run targeted tests after lint-driven edits.
	- **Summary:** Completed. Ran `ruff` and `pylint` on all Task 2 changed Python files and resolved new issues (including migration tooling lint compatibility and config typing fix). Final lint status for changed files: Ruff clean and Pylint `10.00/10`.

7. **Update README and memory** — `status: done`
	- **Description:** revise `README.md` and `ai_memory.md` to reflect changes
	- [ ] Update `README.md` with migration baseline usage and schema status.
	- [ ] Update `ai_memory.md` with durable Task 2 decisions/patterns/fixes in required format.
	- [ ] Ensure documentation reflects current behavior only.
	- **Summary:** Completed. Updated [README.md](README.md) with Task 2 schema/migration baseline details, fixed decisions, and migration command usage. Updated [ai_memory.md](ai_memory.md) with durable Task 2 decisions and migration-testing pattern entries using required date/tag format.

## Clarifying Questions
Q1: For Task 2, should we implement a full column-level schema for all MVP tables now, or a minimal baseline focused on identity/provenance fields with later tasks extending columns?
A1: Implement a full column-level schema for all MVP tables now.

Q2: Preferred UUID strategy for primary keys in PostgreSQL: database-generated UUIDs (`gen_random_uuid`) or application-generated UUIDs?
A2: Database-generated UUIDs with `gen_random_uuid()`.
