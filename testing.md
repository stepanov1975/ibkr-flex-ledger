# Testing policy

## Test integrity

- Every new logical branch or bug fix must have a corresponding pytest case.
- Test filenames describe the feature domain: `test_<module>_<behavior>.py`.
- Prefer the smallest test that reproduces the behavior, then run the affected
  module and the full suite before completion.

## Commands

Run the full Python test suite:

```bash
.venv/bin/pytest -q
```

Run one module or test while iterating:

```bash
.venv/bin/pytest -q tests/test_jobs_reprocess.py
.venv/bin/pytest -q tests/test_jobs_reprocess.py::test_scoped_reprocess_does_not_cleanup_unsupported_dates
```

Database integration tests use the configured `DATABASE_URL`. The local Docker
PostgreSQL guidance is documented in `docs/migrations.md`.

## Handling failures

1. Reproduce the failure and identify whether it is an intended contract change
   or a code defect.
2. Update assertions only when the requested behavior intentionally changed.
3. Fix application logic when behavior violates the current contract.
4. Stop and ask the user when the intended behavior cannot be established from
   the request and authoritative documentation.

Never remove assertions or add broad exception handling merely to make a test
pass.
