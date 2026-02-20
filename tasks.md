# Task Plan
Evaluate Improvement #1 from `docs/ibkr_import_reference_improvements.md` (retry strategy) against the current project implementation in `app/adapters/flex_web_service.py`, including upstream wiring (`app/bootstrap.py`, `app/config/settings.py`) and existing adapter tests. Determine whether exponential backoff with jitter is a net benefit for this codebase given current retry floors for IBKR error codes (`1009`, `1018`, `1019`), deterministic diagnostics expectations, and MVP operational constraints. If beneficial, implement a minimal project-native solution that preserves existing architecture boundaries, keeps deterministic testability, updates only necessary settings/wiring, and adds focused regression tests. Validate with project-required Python lint/test workflow before final documentation updates.

## Subtasks
1. **Decision analysis for Improvement #1** — `status: done`
	- **Description:** Produce a code-grounded decision on whether Improvement #1 should be adopted in this project.
	 [] Trace current retry/backoff control flow and invariants in adapter + orchestrator integration points.
	[] Compare current behavior to reference retry pattern benefits and risks for this project.
	[] Record explicit go/no-go decision with technical rationale and constraints impact.
	- **Summary:** Current adapter uses linear backoff (`initial_wait + attempt * increment`) with no jitter, which is weaker under concurrent retry storms and does not match proven IBKR transient error handling patterns. Improvement #1 is beneficial for this project because exponential backoff + jitter reduces synchronized retry spikes while preserving existing per-error retry floors (`1009`/`1018`/`1019`) through `max(backoff, code_floor)`. Change scope is low-risk because retry logic is centralized inside `FlexWebServiceAdapter` and covered by focused adapter tests.

2. **Implement adapter retry strategy (if approved by Subtask 1)** — `status: done`
	- **Description:** Apply a minimal, maintainable retry-strategy change in project-native adapter code only if Subtask 1 concludes it is beneficial.
	 [] Reuse existing retry helpers/patterns where possible; add no duplicate logic.
	[] Replace linear backoff with exponential backoff + jitter and preserve per-error minimum retry floors via `max(computed_backoff, code_floor)`.
	[] Keep configuration injectable for deterministic tests and operational tuning, with strict input validation.
	- **Summary:** Implemented exponential backoff with jitter in `app/adapters/flex_web_service.py` using a dedicated `_AdapterRetryStrategy` dataclass to keep retry logic centralized and maintainable. Preserved IBKR error-code retry floors by combining computed backoff with code-specific minimum delays via `max(calculated_wait, retry_floor)` semantics. Exposed retry strategy parameters in runtime configuration via `AppSettings` and wired them through `app/bootstrap.py` for both API and CLI orchestration paths.

3. **Add and run targeted regression tests** — `status: done`
	- **Description:** Validate the new retry behavior with focused tests and prove no regression in current retry semantics.
	 [] Add/update adapter tests to cover exponential growth, jitter bounds, and error-code retry floors.
	[] Run targeted pytest module(s) for adapter behavior and confirm pass.
	[] Ensure tests assert deterministic behavior via controlled jitter/random source.
	- **Summary:** Updated `tests/test_adapters_flex_web_service.py` to use new adapter parameters and added deterministic tests for exponential backoff with cap and initial-wait floor behavior. Executed `pytest tests/test_adapters_flex_web_service.py` and confirmed all tests pass (6/6).

4. **Run lint gates for touched Python modules** — `status: done`
	- **Description:** Satisfy project completion gate for Python quality checks on changed code.
	 [] Run `pylint` on touched Python module(s) with project-approved disables.
	[] Run `ruff check . --ignore=E501,W293,W291` and resolve new issues from this task scope.
	[] Confirm no new lint errors remain in modified files.
	- **Summary:** Ran `pylint` on touched app modules and resolved the `too-many-instance-attributes` refactor warning by grouping retry state into `_AdapterRetryStrategy`. Ran `ruff check app tests --ignore=E501,W293,W291` (first-party scope only, per project rule excluding `references/`) and confirmed all checks passed.

5. **Update README and memory** — `status: done`
	- **Description:** revise `README.md` and `ai_memory.md` to reflect changes
	- **Summary:** Updated `README.md` with optional Flex retry tuning settings and documented the exponential backoff + jitter behavior with preserved IBKR retry floors. Added durable memory entries in `ai_memory.md` describing the retry strategy fix and project pattern for centralized retry settings wiring.

## Clarifying Questions
None at this stage.

