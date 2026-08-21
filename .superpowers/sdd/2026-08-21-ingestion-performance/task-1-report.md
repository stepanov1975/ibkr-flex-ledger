# Task 1 Report: Deterministic Polling and Adapter Timings

## Implementation details

- Changed Flex retry scheduling so retry index `0` returns the configured `initial_wait_seconds` without jitter.
- Changed retry indexes greater than zero to apply jitter to the capped exponential base `backoff_base_seconds * 2 ** (retry_index - 1)`.
- Added monotonic millisecond duration measurement for request transport and the complete statement-poll loop.
- Accumulated requested poll sleep seconds, including server-provided minimum retry delays, and emitted integer `statement_poll_wait_duration_ms`.
- Added the exact diagnostic fields `request_transport_duration_ms`, `statement_polling_duration_ms`, and `statement_poll_wait_duration_ms` to completed request/poll stage details.
- Preserved existing poll attempt metadata on download and retry events.

## Files changed

- `app/adapters/flex_web_service.py`
- `tests/test_adapters_flex_web_service.py`

## RED verification

Command:

```bash
/stock_app/.venv/bin/pytest -q tests/test_adapters_flex_web_service.py -k 'fixed_initial_wait or monotonic_durations'
```

Relevant output before implementation:

```text
FF                                                                       [100%]
2 failed, 10 deselected in 0.13s
```

The fixed-wait test observed `15.0` instead of `5.0`; the diagnostics test could not find poll completion details.

## GREEN verification

Focused command:

```bash
/stock_app/.venv/bin/pytest -q tests/test_adapters_flex_web_service.py -k 'fixed_initial_wait or monotonic_durations'
```

Output:

```text
..                                                                       [100%]
2 passed, 10 deselected in 0.13s
```

Complete adapter command:

```bash
/stock_app/.venv/bin/pytest -q tests/test_adapters_flex_web_service.py
```

Output:

```text
............                                                             [100%]
12 passed in 0.18s
```

Full suite command (loads `/stock_app/.env` into the process without printing it):

```bash
if [ -f /stock_app/.env ]; then set -a; . /stock_app/.env; set +a; /stock_app/.venv/bin/pytest -q; else echo '/stock_app/.env missing'; exit 1; fi
```

Output:

```text
........................................................................ [ 62%]
...........................................                              [100%]
115 passed in 4.27s
```

## Self-review

- The first poll is deterministic and does not consume the jitter provider.
- Retry backoff remains capped before jitter, while upstream minimum delays still win through the existing `max` calculation.
- Durations are derived from `perf_counter_ns`, clamped non-negative, and represented as integers.
- Existing request timestamp and poll attempt details remain intact.
- `git diff --check` passed.

## Concerns

No known concerns. The repository does not expose `pytest` as a shell command, so verification used the existing `/stock_app/.venv/bin/pytest` executable.
