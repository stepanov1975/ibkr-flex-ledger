# Task Plan
Evaluate Improvement #4 from `docs/ibkr_import_reference_improvements.md` (persistent HTTP connection pooling for Flex polling) against the current project implementation before any functional change. Current adapter (`app/adapters/flex_web_service.py`) uses `urllib.request.urlopen` per request/poll call, while reference (`references/ngv_reports_ibkr/ngv_reports_ibkr/flex_client.py`) uses a persistent session. Execute one milestone at a time with a hard decision gate: if not beneficial for this codebase, document technical rationale and stop without code changes; if beneficial, apply a minimal project-native implementation with regression coverage and lint/test validation.

## Subtasks
1. **Improvement #4 Deep Comparison and Reuse Audit** — `status: done`
	- **Description:** Build a project-specific comparison between current adapter behavior and reference pooling behavior, including reuse opportunities and constraints.
	- [ ] Re-read Improvement #4 requirements and extract concrete acceptance targets.
	- [ ] Map current transport flow in `app/adapters/flex_web_service.py` (request path, poll loop path, timeout/error mapping, lifecycle expectations).
	- [ ] Inspect reference patterns from repositories listed in `references/REFERENCE_NOTES.md` relevant to persistent HTTP clients and polling.
	- [ ] Produce a concise comparison matrix (benefits, risks, complexity, dependency impact, testability impact) scoped to this project.
	- **Summary:** Current adapter uses `urllib.request.urlopen` per call, so each poll attempt can incur new connection setup. Reference evidence confirms stronger pooling patterns: `ngv_reports_ibkr` uses persistent session (`requests.Session`), `flexquery` uses `urllib3.PoolManager`, while other references are mixed and less robust. Project-fit matrix conclusion: pooling provides measurable operational benefit for frequent poll loops, with low complexity if implemented inside adapter transport boundary only; dependency impact can remain minimal by using already-installed `httpx`.

2. **Decision Gate: NO-GO vs GO** — `status: done`
	- **Description:** Decide whether Improvement #4 is beneficial for this project, based on architecture, operational profile, and maintainability.
	- [ ] Evaluate technical fit with current adapter abstraction and orchestrator contracts.
	- [ ] Evaluate trade-offs (connection reuse gains vs added state/lifecycle complexity and failure modes).
	- [ ] Record explicit decision criteria and final decision.
	- [ ] If NO-GO: document rationale, constraints, and stop execution.
	- [ ] If GO: define minimal change boundary and proceed to next subtask.
	- **Summary:** Decision = GO. Improvement is beneficial because connection reuse targets a real hot path (polling), keeps higher-layer contracts unchanged, and improves efficiency/robustness under retry scenarios. Best project-native option is `httpx.Client` (already in requirements), avoiding new dependency introduction while giving explicit pooling, timeout controls, and maintainable exception mapping.

3. **Implement Project-Native Connection Pooling (Conditional on GO)** — `status: done`
	- **Description:** Introduce persistent HTTP transport pooling in the adapter with the smallest safe diff and no unrelated refactoring.
	- [ ] Reuse existing adapter structure and typed error mapping; avoid changing higher-layer interfaces.
	- [ ] Replace per-call transport with a persistent client approach that supports connection reuse.
	- [ ] Preserve existing timeout semantics, retry behavior, diagnostics timeline behavior, and typed exceptions.
	- [ ] Ensure resource lifecycle is explicit and safe for long-running processes.
	- [ ] Keep dependency choices aligned with project standards and current requirements.
	- **Summary:** Implemented pooled transport in `app/adapters/flex_web_service.py` by replacing per-call `urllib` usage with adapter-owned persistent `httpx.Client`. Kept all existing adapter interfaces and orchestrator-facing behavior unchanged (same request/poll flow, retry logic, stage timeline events, and typed exception mapping). Added explicit lifecycle helpers (`adapter_close`, context-manager enter/exit) to make transport resource handling explicit without touching higher layers.

4. **Regression Tests and Quality Gates (Conditional on GO)** — `status: done`
	- **Description:** Add/adjust targeted tests to validate pooling behavior and run required quality checks.
	- [ ] Add or update tests in `tests/test_adapters_flex_web_service.py` to reproduce and validate the targeted behavior change.
	- [ ] Run focused pytest for adapter tests and confirm pass.
	- [ ] Run `ruff` and `pylint` for touched project files and resolve new issues.
	- [ ] Confirm no unrelated test/lint scope is modified.
	- **Summary:** Updated `tests/test_adapters_flex_web_service.py` for the new `httpx` transport error surface and added regression coverage to verify one persistent client instance is reused across request+poll calls. Validation completed: `pytest tests/test_adapters_flex_web_service.py` passed (9/9), `ruff check` passed on touched files, and `pylint` passed (10.00/10) on touched files.

5. **Update README and memory (GO path only)** — `status: done`
	- **Description:** revise `README.md` and `ai_memory.md` only when implementation changes are applied.
	- [ ] Execute this subtask only if Subtask 2 decision is GO.
	- [ ] Update `README.md` only if behavior/configuration/operational guidance changed.
	- [ ] Add durable decision/pattern/fix entry to `ai_memory.md` using required `- [YYYY-MM-DD] {TAG} :: ...` format.
	- [ ] Ensure documentation reflects final reality only (no transitional notes).
	- **Summary:** `README.md` required no change because user-facing configuration and operational behavior remain the same. Added durable architecture decision entry to `ai_memory.md` documenting Improvement #4 GO adoption and the project-native pooled `httpx.Client` transport pattern.

## Clarifying Questions
Q1: If the decision is NO-GO, do you want me to still complete Subtask 5 with memory-only documentation of the decision?
A1: No. If decision is NO-GO, stop after documenting rationale in Subtask 2 and do not execute Subtask 5.

Q2: If the decision is GO, do you prefer using the already-installed `httpx` client for pooling, or adding `requests` to mirror the reference style?
A2: Choose the best option for this codebase during implementation design (bias to minimal dependency and maintainability).
