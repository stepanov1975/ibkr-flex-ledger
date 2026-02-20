# IBKR Import Improvements Derived from Reference Verification

Date: 2026-02-20

This document includes only improvements where reference implementations are verifiably stronger for IBKR import concerns.

1. Replace linear retry backoff with exponential backoff plus jitter

   Reference project:
   - `ngv_reports_ibkr`

   Why reference is better:
   - `references/ngv_reports_ibkr/ngv_reports_ibkr/flex_client.py` uses exponential backoff with jitter (`base * (2**attempt)` with random multiplier), which reduces synchronized retry spikes.
   - Current project adapter (`app/adapters/flex_web_service.py`) uses linear retry growth and no jitter. This increases the chance of synchronized retries against IBKR under `1009`/`1018`/`1019` conditions.

   How to improve this project:
   - Replace the current linear wait formula with exponential backoff capped by max delay.
   - Add jitter (for example 0.5x to 1.5x multiplier or decorrelated jitter).
   - Preserve code-specific minimum retry hints (for example throttling floor), but combine as `max(computed_backoff, code_floor_seconds)` so error-specific guidance remains effective across all attempts.
   - Expose retry strategy parameters in adapter config for deterministic testing and tuning.

2. Introduce a typed Flex exception hierarchy (phase-aware, action-aware)

   Reference project:
   - `ngv_reports_ibkr`

   Why reference is better:
   - `ngv_reports_ibkr` defines `FlexRequestError`, `FlexStatementError`, `FlexRetryableError`, `FlexTokenError`, and `FlexTokenExpiredError`.
   - Current project adapter mostly raises built-ins (`ValueError`, `RuntimeError`, `ConnectionError`, `TimeoutError`) which carry less semantic intent for orchestrators.

   How to improve this project:
   - Add project-native adapter exception classes in a dedicated module (for example `app/adapters/flex_errors.py`).
   - Raise request-phase and statement-phase errors explicitly so orchestrator diagnostics and policies can branch by failure type.
   - Keep built-in transport exceptions mapped into typed adapter exceptions with original cause chaining.

3. Separate token lifecycle errors (`1012` expired vs `1015` invalid)

   Reference project:
   - `ngv_reports_ibkr`

   Why reference is better:
   - `ngv_reports_ibkr` classifies token errors and distinguishes expired-token handling (`FlexTokenExpiredError`) from other token failures.
   - Current project maps both token codes to generic failure paths, reducing ability to automate token lifecycle behavior.

   How to improve this project:
   - Map `1012` to an explicit expired-token exception and `1015` to explicit invalid-token exception.
   - In orchestrator policy, allow targeted remediation hooks (for example refresh-token workflow only for expired-token errors).
   - Persist deterministic error codes in run diagnostics so operations can separate credential rotation issues from transient service issues.

4. Use persistent HTTP connection pooling for Flex polling

   Reference project:
   - `ngv_reports_ibkr`

   Why reference is better:
   - `ngv_reports_ibkr` uses `requests.Session`, enabling connection reuse across request and poll calls.
   - Current project uses `urllib.request.urlopen` per call, which typically re-establishes connections for each poll attempt.

   How to improve this project:
   - Switch adapter transport to a persistent client (`requests.Session` or `urllib3.PoolManager`).
   - Initialize headers and timeout once per adapter instance.
   - Keep transport abstraction local to adapter so higher layers remain unchanged.

5. Centralize error-code semantics with enum and classification sets

   Reference project:
   - `ngv_reports_ibkr`

   Why reference is better:
   - `ngv_reports_ibkr` uses a `FlexErrorCode` enum plus explicit sets for retryable and token-related codes, making routing logic clearer and harder to misuse.
   - Current project has a message map and constants, but routing logic is more distributed and less strongly typed.

   How to improve this project:
   - Define a project-native `Enum` for known Flex codes.
   - Centralize classification sets (`retryable`, `token`, `fatal`) and reuse them in both request and poll paths.
   - Keep one canonical mapping from code to default message and one canonical mapping from code to exception type.

## Verification scope

The conclusions above were verified against:
- `app/adapters/flex_web_service.py`
- `references/ngv_reports_ibkr/ngv_reports_ibkr/flex_client.py`
- `references/ibflex/ibflex/client.py`
- `references/ibflex2/ibflex/client.py`
- `references/flexquery/flexquery/flexquery.py`
