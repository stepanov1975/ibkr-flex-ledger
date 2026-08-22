# Python linting and type checking

The active Python quality gates are Ruff and MyPy. Pylint is not part of the
project toolchain.

## Ruff

Run lint checks across runtime code and tests:

```bash
.venv/bin/ruff check app tests
```

Ruff formatting is not currently an enforced project gate. Do not bulk-format
existing files as part of an unrelated change.

## MyPy

Run the configured first-party runtime check:

```bash
.venv/bin/mypy
```

`mypy.ini` defines the checked scope as `app/`. Targeted strict checks may be
used for changed tests when a task explicitly requires them.

## Suppressions

- Prefer correcting the type contract over adding `# type: ignore`.
- Every ignore must name its exact error code and explain why the runtime
  behavior cannot be expressed more accurately.
