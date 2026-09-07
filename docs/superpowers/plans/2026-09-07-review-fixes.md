# Code Review Fixes Implementation Plan

**Goal:** Correct all 14 review findings and both smaller observations from the September 7 review, with regression coverage.

**Architecture:** Preserve the existing layers and PostgreSQL repositories. Fix current flows with narrow changes; do not rewrite the application. Keep raw artifacts immutable and preserve deterministic replay.

**Spec:** The user approved the corrections in `/tmp/stock-app-code-review-2026-09-07.md` by asking to fix all issues.

**Constraints:** Fixed USD base, single account, database access only in db modules, no imports from references. No live broker requests or external alert sends in tests. Use temporary databases for validation. Source changes do not authorize production data repair or destructive WAL cleanup during development.

## Task 1: Ingestion and mapping integrity

- [x] Reproduce failed canonical-write/skip sequences for exact and distinct subsequent artifacts, then fix skip invalidation or transactional publication.
- [x] Preserve FX conversion history with date/pair synthetic identity, including handling pre-fix synthetic events during replay.
- [x] Resolve recovered artifact snapshot ownership from actual raw rows during replay.
- [x] Correct CASHDIV automatic classification: represent supported cash effects without duplication or require explicit manual review when safe matching cannot be established.
- [x] Run relevant unit and isolated PostgreSQL regressions.

## Task 2: Ledger calculations

- [x] Preserve and independently convert commission currency, including missing-FX state.
- [x] Preserve numeric broker transaction ordering for tied timestamps.
- [x] Adjust historical fallback marks after splits and preserve provisional marking for fallback valuation.
- [x] Run ledger regression tests for reported examples and relevant edge cases.

## Task 3: Reporting and portfolio workflows

- [x] Reconcile the same cumulative activity period using deduplicated event source rows; normalize broker expense signs.
- [x] Return HTTP 409 when label deletion would orphan a label-only note; preserve dual-target notes and ordinary deletion.
- [x] Distinguish omitted label color PATCH from explicit null.
- [x] Show provisional state on portfolio rows and aggregate totals.
- [x] Run real database workflow tests and execute shipped UI JavaScript tests.

## Task 4: Operations and deployment

- [x] Initial loopback restriction superseded: preserve direct LAN access confirmed by the user; deployment and LAN API checks passed.
- [x] Treat partial SMTP refusal as delivery failure, preserving retry eligibility.
- [x] Escape database URL percent characters at Alembic configuration boundary.
- [x] Prune archived WAL using a verified retained recovery baseline, with fail-closed handling and tests against disposable directories.
- [x] Address installed dependency advisories through checked requirements where applicable, verifying compatibility.

## Task 5: Integration and review

- [x] Review each task's diff and validate against its acceptance checks.
- [x] Run full suite against a freshly migrated temporary PostgreSQL database, Ruff, MyPy, shell checks and dependency audit.
- [x] Run an independent final review and resolve actionable findings.
- [x] Document migration/replay and deployment steps needed for existing installations; report validation and remaining operational limits.

## Decisions and progress

- Implement on `codex/review-fixes` in the shared checkout so results remain directly reviewable.
- Label deletion preserves notes and reports a conflict rather than silently deleting content.
- SMTP retries may redeliver to recipients that already accepted a partially refused message; this is preferable to permanently dropping an alert and will be documented.
- Task ownership will avoid concurrent edits to shared interfaces; ledger work follows ingestion changes.

- Completed: 364 tests passed against temporary PostgreSQL databases; Ruff, MyPy, shell syntax, dependency consistency/audit, and Docker image build passed. Independent final review approved the changes.
- Changes remain uncommitted on the review branch; deployment and historical replay were not performed.
