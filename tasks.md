# Task Plan
Evaluate Improvement #5 from `docs/ibkr_import_reference_improvements.md`: centralize IBKR Flex error-code semantics with a project-native enum and shared classification sets. The current adapter already includes typed exceptions and distributed code constants/sets in `app/adapters/flex_web_service.py`; the task is to verify whether further centralization is genuinely beneficial for this codebase. Execution must proceed milestone-by-milestone with a hard decision gate: if not beneficial, document technical rationale and stop without code changes; if beneficial, implement the smallest project-native change, add focused regression coverage, run required quality gates, and then update project docs/memory.

## Subtasks
1. **Analyze Improvement #5 and Current Implementation** — `status: done`
   - **Description:** Build a fact-based baseline of current behavior before any decision or code change.
   - [x] Re-read Improvement #5 requirements in `docs/ibkr_import_reference_improvements.md` and extract concrete expected outcomes.
   - [x] Inspect current project implementation in `app/adapters/flex_web_service.py`, `app/adapters/flex_errors.py`, and relevant tests for error-code mapping/classification usage.
   - [x] Identify existing reuse points and duplication hotspots to satisfy DRY with minimal net-new code.
   - [x] Produce a concise current-state summary with explicit references to where semantics are currently defined.
   - **Summary:** Current semantics are partially centralized but still adapter-local and stringly typed: canonical fallback messages live in `_KNOWN_FLEX_ERROR_MESSAGES`, retry classification in `_RETRYABLE_POLL_ERROR_CODES`, token routing via `_TOKEN_EXPIRED_ERROR_CODE`/`_TOKEN_INVALID_ERROR_CODE`, plus separate branching in `_adapter_retry_delay_seconds_for_error()` and `_adapter_raise_request_error()` inside `app/adapters/flex_web_service.py`. Typed exceptions are already strong in `app/adapters/flex_errors.py`. Test coverage in `tests/test_adapters_flex_web_service.py` verifies selected code-based behavior (`1009`, `1018`, `1012`) and non-retryable branching, but there is no single exported canonical code model reused across adapter decision points.

2. **Reference Audit Across Approved Repositories** — `status: done`
   - **Description:** Validate reference patterns from repositories listed in `references/REFERENCE_NOTES.md` for similar error-code centralization design.
   - [x] Inspect `references/ngv_reports_ibkr` implementation details for enum-based code semantics and classification sets.
   - [x] Check other relevant approved references for comparable patterns and trade-offs (for example maintainability/testability impacts).
   - [x] Record what is reusable as architecture ideas (not runtime imports/copy).
   - [x] Produce a comparison summary: reference strength vs project fit.
   - **Summary:** `references/ngv_reports_ibkr/ngv_reports_ibkr/flex_client.py` provides the strongest relevant pattern: `FlexErrorCode` enum plus centralized `RETRYABLE_ERROR_CODES` and `TOKEN_ERROR_CODES`, reused in one `_raise_for_error()` decision point for consistent routing. `references/flexquery/flexquery/flexquery.py` confirms pooled transport but does not provide comparable enum-based classification centralization. Reusable architecture idea for this project is to keep one canonical code model and reusable membership checks while retaining project-native typed exceptions and avoiding any runtime dependency on reference code.

3. **Decision Gate: Beneficial vs Not Beneficial** — `status: done`
   - **Description:** Decide whether Improvement #5 should be adopted in this project.
   - [x] Compare current vs reference by correctness, robustness, clarity, and maintenance cost in this architecture.
   - [x] Evaluate risks (over-engineering, churn, behavior drift, unnecessary abstraction) and constraints (single-site deployment, no backward-compat layer).
   - [x] If NOT beneficial: document technical reasons and stop execution with no code changes.
   - [x] If beneficial: define the minimal safe change boundary and acceptance criteria for implementation.
   - **Summary:** Decision = GO. Improvement #5 is beneficial because current behavior is correct but semantics are split across multiple adapter-local constants and conditional branches, increasing change risk when new codes are added. A minimal project-native centralization (single adapter module with enum + classification/message maps and helper predicates) improves clarity and reuse with low churn. Acceptance boundary: preserve all existing runtime behavior (same typed exceptions, retry/token routing outcomes, diagnostics structure, and error messages), with focused code movement only.

4. **Implement Minimal Project-Native Centralization (Conditional on GO)** — `status: done`
   - **Description:** Implement only if Subtask 3 concludes GO.
   - [x] Introduce/extend a single canonical error-code model (enum + classification sets) in the smallest appropriate adapter-layer location.
   - [x] Reuse existing typed exceptions and route request/poll classification through the canonical model.
   - [x] Preserve all current external behavior contracts (exception types, diagnostics semantics, retry policy, orchestrator compatibility).
   - [x] Keep diffs focused to Improvement #5 only; avoid unrelated refactoring.
   - **Summary:** Added `app/adapters/flex_error_codes.py` as the single canonical source for known Flex error semantics: `FlexErrorCode` enum, default message map, and classification sets (`FLEX_RETRYABLE_POLL_CODES`, `FLEX_TOKEN_CODES`, `FLEX_FATAL_CODES`) with shared helper functions. Updated `app/adapters/flex_web_service.py` to consume this module for fallback message resolution, retryable classification checks, retry-delay overrides, and token error routing (`1012`/`1015`) while preserving existing typed exceptions and adapter public behavior.

5. **Regression Tests and Quality Gates (Conditional on GO)** — `status: done`
   - **Description:** Validate behavior for the specific improvement with focused automated checks.
   - [x] Add/adjust targeted tests in existing adapter test modules to cover new centralized classification behavior.
   - [x] Run focused pytest scope for touched behavior and confirm pass/fail expectations.
   - [x] Run `ruff` and `pylint` per project protocol on touched files and resolve new issues.
   - [x] Confirm no unrelated functional behavior changed.
   - **Summary:** Added `tests/test_adapters_flex_error_codes.py` for centralized semantics coverage (set disjointness, fatal-set composition, message fallback, retry-delay routing). Validation executed successfully: `pytest tests/test_adapters_flex_web_service.py tests/test_adapters_flex_error_codes.py` (13 passed), `ruff check` (all checks passed), and `pylint` on touched files (10.00/10). No behavior outside adapter error-classification semantics was changed.

6. **Update README and memory** — `status: done`
   - **Description:** revise `README.md` and `ai_memory.md` to reflect finalized outcomes.
   - [x] Update `README.md` if operational/developer-facing behavior changed.
   - [x] Add durable entry to `ai_memory.md` using required format `- [YYYY-MM-DD] {TAG} :: ...`.
   - [x] Ensure docs reflect final current reality only.
   - **Summary:** `README.md` required no update because runtime interfaces and user-facing operations did not change. Added durable decision entry to `ai_memory.md` documenting Improvement #5 GO adoption and the new centralized adapter error-code semantics module/pattern.

## Clarifying Questions
None at this stage.
