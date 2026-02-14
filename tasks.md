# Task Plan
Task 6 implements deterministic valuation and FX fallback hierarchies from the frozen spec on top of completed Task 5 canonical mapping. Current verified state: canonical persistence exists for `event_trade_fill`, `event_cashflow`, `event_fx`, and `event_corp_action`; Task 5 mapping currently writes FX events only from `ConversionRates` rows and does not yet apply the full frozen FX fallback order (`Trades.fxRateToBase` -> `netCashInBase/netCash` -> `ConversionRates`). There is also no implemented valuation resolver yet for the frozen EOD mark hierarchy (`OpenPositions.markPrice` -> same-day `Trades.closePrice` -> last `Trades.tradePrice` on/before report date). Task 6 must add reusable project-native fallback services, deterministic tie-break behavior, and diagnostics codes (`EOD_MARK_FALLBACK_LAST_TRADE`, `EOD_MARK_MISSING_ALL_SOURCES`, `FX_RATE_MISSING_ALL_SOURCES`) with minimal diff by extending existing mapping/db/job patterns rather than introducing parallel pipelines.

Resume notes if interrupted:
- Continue from the first subtask still in `status: planned`.
- Keep DB access in `app/db/*` only and reuse existing canonical pipeline wiring.
- Keep reference usage as pattern-only (never import from `references/`).
- Treat `MVP_spec_freeze.md` section 2 as normative for ordering and tie-break rules.

## Subtasks
1. **Reference and reuse audit for Task 6 extension points** — `status: planned`
   - **Description:** Confirm exact modules, contracts, and reference patterns to extend so Task 6 is implemented with the smallest safe diff.
   - [ ] Re-audit project-native extension points in `app/mapping/service.py`, `app/jobs/canonical_pipeline.py`, `app/db/interfaces.py`, and `app/db/canonical_persistence.py`.
   - [ ] Re-check `references/REFERENCE_NOTES.md` and inspect relevant patterns in `references/finx-reports-ib` and `references/ibflex` for mark/FX source fields and tie-break signals.
   - [ ] Produce implementation notes in this file summary indicating exactly which existing modules will be extended and which new modules (if any) are justified.
   - **Acceptance criteria:** The chosen implementation path is explicitly tied to existing project modules and avoids duplicate logic paths.
   - **Summary:** (fill after completion)

2. **Create failing regression tests for frozen fallback hierarchies** — `status: planned`
   - **Description:** Add red tests first to capture missing Task 6 behavior before modifying runtime code.
   - [ ] Add tests for EOD mark fallback source order and tie-break rules, including `EOD_MARK_FALLBACK_LAST_TRADE` and `EOD_MARK_MISSING_ALL_SOURCES` outcomes.
   - [ ] Add tests for FX fallback source order (`fxRateToBase` -> derived ratio -> `ConversionRates`) and tie-break rules (exact date, nearest previous, latest `ingestion_run_id`, highest raw record primary key).
   - [ ] Add tests for provisional behavior when non-base-currency FX has no available source and ensure `FX_RATE_MISSING_ALL_SOURCES` is emitted.
   - **Acceptance criteria:** New Task 6 tests fail on current code and are deterministic across repeated runs.
   - **Summary:** (fill after completion)

3. **Extend typed contracts and DB read methods for fallback inputs** — `status: planned`
   - **Description:** Add explicit typed contracts and repository methods needed to resolve mark and FX fallbacks without ad-hoc dictionaries.
   - [ ] Extend `app/db/interfaces.py` with read models for valuation and FX candidate rows (OpenPositions, Trades, ConversionRates) and deterministic diagnostics outputs.
   - [ ] Add/extend DB read repository methods in `app/db/canonical_persistence.py` to fetch candidate rows keyed by account, conid, currency pair, and report date.
   - [ ] Reuse existing repository/service naming conventions and keep SQL isolated to `app/db/*`.
   - **Acceptance criteria:** Contracts and repository ports fully represent Task 6 fallback inputs/outputs and are consumed by services without raw dict unpacking.
   - **Summary:** (fill after completion)

4. **Implement reusable valuation and FX fallback services** — `status: planned`
   - **Description:** Implement deterministic fallback logic as reusable services for Task 6 and upcoming Task 7 ledger/snapshot work.
   - [ ] Implement EOD mark resolver with frozen priority order, tie-breaks, and deterministic diagnostic/provisional outputs.
   - [ ] Implement FX resolver with frozen priority order, half-even rounding for derived ratio to 10 decimals, and deterministic diagnostic/provisional outputs.
   - [ ] Add FSN notes and runtime guards only where non-obvious constraints are necessary to prevent regression loops.
   - **Acceptance criteria:** Services return stable outputs for identical inputs and encode source + diagnostic decisions exactly per frozen spec.
   - **Summary:** (fill after completion)

5. **Integrate Task 6 fallback engine into canonical pipeline flow** — `status: planned`
   - **Description:** Wire fallback services into existing canonical mapping/persistence flow without creating a parallel orchestration path.
   - [ ] Replace current FX-only-from-ConversionRates behavior with full Task 6 FX fallback resolution in canonical pipeline mapping outputs.
   - [ ] Persist resolved FX values/source/provisional/diagnostic fields into `event_fx` via existing canonical persistence APIs.
   - [ ] Ensure ingestion/reprocess timelines include Task 6-relevant diagnostics summary counters where appropriate.
   - **Acceptance criteria:** Ingestion and reprocess flows both produce deterministic FX fallback results and diagnostics from the same shared path.
   - **Summary:** (fill after completion)

6. **Run Task 6 validation gates (tests and lint)** — `status: planned`
   - **Description:** Validate Task 6 changes with project testing and linting protocols.
   - [ ] Run targeted Task 6 tests first, then adjacent ingestion/mapping/canonical tests impacted by wiring changes.
   - [ ] Run `ruff` on project code (excluding `references/`) and resolve all new issues.
   - [ ] Run `pylint` per project protocol on Task 6 touched modules and resolve all new issues.
   - **Acceptance criteria:** Task 6 regression suite passes and lint gates pass with zero new errors in changed code.
   - **Summary:** (fill after completion)

7. **Update README and memory** — `status: planned`
   - **Description:** revise `README.md` and `ai_memory.md` to reflect changes
   - [ ] Update `README.md` Task 6 section with implemented fallback hierarchies, diagnostics semantics, and affected workflow surfaces.
   - [ ] Add durable Task 6 decisions/patterns to `ai_memory.md` using required format.
   - [ ] Remove stale documentation that still describes Task 6 functionality as missing.
   - **Acceptance criteria:** Documentation and memory reflect actual Task 6 implementation and no longer contradict runtime behavior.
   - **Summary:** (fill after completion)

## Clarifying Questions
Q1: For EOD mark outputs in Task 6, should the resolver persist intermediate mark decisions in DB now, or return deterministic outputs for immediate Task 6 tests and Task 7 consumption without new persistence tables?
A1: Pending information from user.

Q2: For tie-break rule "highest raw record primary key", should UUID ordering (`raw_record_id` descending) be treated as the canonical implementation of "highest" in this codebase?
A2: Pending information from user.
