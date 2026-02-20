"""Regression tests for centralized Flex error-code semantics."""

from __future__ import annotations

from app.adapters.flex_error_codes import (
    FLEX_FATAL_CODES,
    FLEX_RETRYABLE_POLL_CODES,
    FLEX_TOKEN_CODES,
    FlexErrorCode,
    flex_error_default_message,
    flex_error_retry_delay_seconds,
)


def test_adapters_flex_error_codes_retryable_and_token_sets_are_disjoint() -> None:
    """Ensure token and retryable classifications never overlap.

    Args:
        None: This test uses module-level constants only.

    Returns:
        None: Assertions validate classification-set separation.

    Raises:
        AssertionError: Raised when classification sets overlap.
    """

    assert FLEX_RETRYABLE_POLL_CODES.isdisjoint(FLEX_TOKEN_CODES)


def test_adapters_flex_error_codes_fatal_set_excludes_retryable_and_token_codes() -> None:
    """Ensure fatal codes do not include retryable or token classifications.

    Args:
        None: This test uses module-level constants only.

    Returns:
        None: Assertions validate fatal-set composition.

    Raises:
        AssertionError: Raised when fatal set composition is incorrect.
    """

    assert FLEX_FATAL_CODES.isdisjoint(FLEX_RETRYABLE_POLL_CODES)
    assert FLEX_FATAL_CODES.isdisjoint(FLEX_TOKEN_CODES)


def test_adapters_flex_error_codes_known_message_and_unknown_fallback() -> None:
    """Resolve known default messages and preserve fallback for unknown codes.

    Args:
        None: This test uses deterministic helper inputs.

    Returns:
        None: Assertions validate message lookup behavior.

    Raises:
        AssertionError: Raised when message lookup behavior is incorrect.
    """

    assert flex_error_default_message(FlexErrorCode.INVALID_TOKEN.value, "fallback") == "Token is invalid."
    assert flex_error_default_message("UNKNOWN", "fallback") == "fallback"


def test_adapters_flex_error_codes_retry_delay_overrides_match_contract() -> None:
    """Return expected code-specific retry delay floors.

    Args:
        None: This test uses deterministic helper inputs.

    Returns:
        None: Assertions validate retry-delay routing contract.

    Raises:
        AssertionError: Raised when retry-delay routing is incorrect.
    """

    assert flex_error_retry_delay_seconds(FlexErrorCode.RATE_LIMITED.value) == 10
    assert flex_error_retry_delay_seconds(FlexErrorCode.SERVER_BUSY.value) == 5
    assert flex_error_retry_delay_seconds(FlexErrorCode.STATEMENT_IN_PROGRESS.value) == 5
