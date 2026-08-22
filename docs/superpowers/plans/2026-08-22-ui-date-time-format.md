# UI Date and Time Format Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display every current dashboard date as `dd/mm/yy` and timestamp as `dd/mm/yy hh:mm` in 24-hour `Asia/Jerusalem` time.

**Architecture:** Keep formatting in the dependency-free inline dashboard script. Reformat business dates without timezone conversion and format UTC instants through `Intl.DateTimeFormat` with the established UI timezone.

**Tech Stack:** FastAPI HTML response, browser JavaScript, pytest

**Spec:** `docs/superpowers/specs/2026-08-22-ui-date-time-format-design.md`

## Global Constraints

- UI dates are zero-padded `dd/mm/yy`.
- UI timestamps are zero-padded `dd/mm/yy hh:mm` with 24-hour time.
- Timestamp display uses `Asia/Jerusalem`; date-only business values do not undergo timezone conversion.
- API and persistence timestamp formats remain unchanged.
- No new runtime dependency is introduced.

---

### Task 1: Dashboard formatting and documentation

**Files:**
- Modify: `tests/test_api_ui.py`
- Modify: `app/api/routers/ui.py`
- Modify: `MVP_spec_freeze.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: ISO date strings and UTC timestamp strings already returned by dashboard APIs.
- Produces: `formatDate(value)` and `formatDateTime(value)` browser helpers returning the specified display strings or `—`.

- [x] **Step 1: Write failing dashboard regression tests**

  Add tests requiring the `en-GB` locale, `Asia/Jerusalem` timezone, 24-hour two-digit fields, and formatter use for `report_date_local`, `created_at_utc`, and `started_at_utc`.

- [x] **Step 2: Verify the tests fail for the missing formatting contract**

  Run: `.venv/bin/pytest tests/test_api_ui.py -q`

  Expected: FAIL because the dashboard does not yet define or apply the date/time formatters.

- [x] **Step 3: Implement the minimal browser formatting helpers and call sites**

  Add `formatDate(value)` for date-only strings and `formatDateTime(value)` using `Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Jerusalem', day:'2-digit', month:'2-digit', year:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23'})`. Apply them only to the three current dashboard date/timestamp fields.

- [x] **Step 4: Document the normative and user-facing display convention**

  Add the precise format, timezone, and API/storage boundary to `MVP_spec_freeze.md` and README dashboard documentation.

- [x] **Step 5: Run focused and full verification**

  Run: `.venv/bin/pytest tests/test_api_ui.py -q`

  Run: `.venv/bin/pytest -q`

  Expected: all tests pass.

- [ ] **Step 6: Commit the completed change**

  ```bash
  git add tests/test_api_ui.py app/api/routers/ui.py MVP_spec_freeze.md README.md docs/superpowers/specs/2026-08-22-ui-date-time-format-design.md docs/superpowers/plans/2026-08-22-ui-date-time-format.md
  git commit -m "feat: standardize UI date and time formats"
  ```
