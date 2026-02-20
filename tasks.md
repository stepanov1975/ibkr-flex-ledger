# Task Plan
Evaluate Improvement #2 from [docs/ibkr_import_reference_improvements.md](docs/ibkr_import_reference_improvements.md) (typed Flex exception hierarchy) against the current adapter-orchestrator error flow, then implement only if it is a net maintainability and robustness gain for this codebase. The work must stay strictly within adapter/orchestrator error semantics and tests, avoid unrelated refactors, reuse existing patterns, and follow project testing/linting gates. If the analysis concludes the change is not beneficial, no code changes are applied and execution stops with a documented technical rationale.

## Subtasks
1. **Decision gate: evaluate Improvement #2 fit** — `status: done`
	 - **Description:** Perform a focused comparison between reference and project-native implementation, then decide go/no-go based on architecture fit and operational impact.
		 - [ ] Review Improvement #2 details and reference behavior in `references/ngv_reports_ibkr`.
		 - [ ] Trace current exception flow in adapter and job orchestrators, including error-code mapping and diagnostics behavior.
		 - [ ] Produce a written decision with explicit trade-offs, risks, and impact scope.
	 - **Acceptance Criteria:** A clear documented decision exists: either (a) stop with technical rationale and no code changes, or (b) proceed with a scoped implementation design.
	 - **Summary:** GO decision. Improvement #2 is a net benefit because current adapter errors are raised as built-ins (`ValueError`/`RuntimeError`/`ConnectionError`/`TimeoutError`), which forces orchestrators to classify broad categories and loses phase-specific intent (request vs statement vs token lifecycle). A project-native typed exception hierarchy improves robustness and maintainability by centralizing semantics at adapter boundary, preserving cause chaining, enabling more deterministic run error codes/triage, and keeping behavior backward-compatible at successful-path and retry-policy levels.

2. **Implement typed adapter exception hierarchy** — `status: done`
	 - **Description:** If Subtask 1 is go, add project-native typed exceptions and integrate them in Flex adapter request/poll/transport paths with cause chaining.
		 - [ ] Reuse existing adapter structure and add a dedicated exception module in `app/adapters/`.
		 - [ ] Replace generic built-in raises in adapter with phase-aware typed exceptions while preserving current messages/error codes.
		 - [ ] Keep retry semantics and stage timeline behavior unchanged except for improved exception typing.
	 - **Acceptance Criteria:** Adapter raises typed exceptions for request, statement, retryable, and token lifecycle failures without changing successful fetch behavior.
	 - **Summary:** Added project-native typed Flex adapter exceptions in `app/adapters/flex_errors.py` and exported them from adapter package API. Updated `app/adapters/flex_web_service.py` to raise typed request/statement/transport/token exceptions with preserved upstream messages and error codes, plus cause chaining for transport/XML failures. Retry behavior and successful fetch flow remain unchanged.

3. **Align orchestrator error policy and diagnostics** — `status: done`
	 - **Description:** If Subtask 2 is done, update orchestrator exception-to-error-code mapping and failure diagnostics to use typed exception semantics.
		 - [ ] Extend ingestion/reprocess exception handling branches to classify adapter-typed failures deterministically.
		 - [ ] Preserve existing run finalization contract and timeline shape.
		 - [ ] Ensure token/request/statement failures map to stable error codes that improve operational triage.
	 - **Acceptance Criteria:** Failed runs retain deterministic finalization and now classify typed adapter failures in a more precise, policy-aligned way.
	 - **Summary:** Updated orchestrator error classification to consume typed adapter failures. `IngestionJobOrchestrator` now maps token/request/statement adapter errors to dedicated deterministic codes (`INGESTION_TOKEN_EXPIRED_ERROR`, `INGESTION_TOKEN_INVALID_ERROR`, `INGESTION_REQUEST_ERROR`, `INGESTION_STATEMENT_ERROR`) while preserving existing timeout/connection/contract fallbacks. `CanonicalReprocessOrchestrator` mapping was aligned similarly for deterministic future-proof classification without changing run-finalization/timeline contracts.

4. **Regression tests and quality gates** — `status: done`
	 - **Description:** Add or update tests to prove new behavior and run required test/lint checks for changed Python modules.
		 - [ ] Add targeted tests that reproduce and validate typed exception behavior in adapter and orchestrator flows.
		 - [ ] Run targeted pytest modules relevant to changed files.
		 - [ ] Run `pylint` and `ruff` per project protocol; fix all new issues introduced by this work.
	 - **Acceptance Criteria:** Relevant tests pass, and linting passes with zero new errors for the changed scope.
	 - **Summary:** Added targeted regression tests for typed exception behavior in adapter and orchestrator flows. Verified with `pytest` on `test_adapters_flex_web_service.py`, `test_jobs_ingestion_orchestrator.py`, and `test_jobs_reprocess.py` (all pass). Ran `ruff` on changed files (pass). Ran `pylint` on changed scope; only pre-existing cross-file duplicate-code signal (`R0801`) remained, and non-duplicate checks pass cleanly.

5. **Update README and memory** — `status: done`
	 - **Description:** revise `README.md` and `ai_memory.md` to reflect changes
		 - [ ] Update `README.md` only if operator-visible behavior/contracts changed.
		 - [ ] Add durable decision/pattern/fix entries to `ai_memory.md` using required format and date.
		 - [ ] Remove or avoid stale notes so memory reflects current code reality.
	 - **Acceptance Criteria:** Documentation and memory are consistent with final implementation decision and code behavior.
	 - **Summary:** Updated `README.md` ingestion baseline to reflect typed Flex adapter failure classification with deterministic error-code routing. Updated `ai_memory.md` with durable decision/pattern entries documenting project-native typed adapter exceptions and orchestrator mapping policy.

## Clarifying Questions
Q1: No blocking clarifications identified at planning stage. May implementation proceed with project-native exception names (not necessarily reference-identical names) if Subtask 1 is go?
A1: Approved. Implementation may proceed with project-native exception names.
