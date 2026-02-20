"""Canonical IBKR Flex error-code semantics for adapter-layer routing."""

from __future__ import annotations

from enum import Enum
from typing import Final


class FlexErrorCode(str, Enum):
    """Known IBKR Flex API error codes used by adapter routing logic."""

    STATEMENT_NOT_AVAILABLE = "1003"
    STATEMENT_INCOMPLETE = "1004"
    SETTLEMENT_NOT_READY = "1005"
    FIFO_NOT_READY = "1006"
    MTM_NOT_READY = "1007"
    MTM_AND_FIFO_NOT_READY = "1008"
    SERVER_BUSY = "1009"
    LEGACY_QUERY_UNSUPPORTED = "1010"
    SERVICE_ACCOUNT_INACTIVE = "1011"
    TOKEN_EXPIRED = "1012"
    IP_RESTRICTION = "1013"
    INVALID_QUERY = "1014"
    INVALID_TOKEN = "1015"
    INVALID_ACCOUNT = "1016"
    INVALID_REFERENCE_CODE = "1017"
    RATE_LIMITED = "1018"
    STATEMENT_IN_PROGRESS = "1019"
    INVALID_REQUEST = "1020"
    STATEMENT_UNAVAILABLE = "1021"


FLEX_ERROR_DEFAULT_MESSAGES: Final[dict[str, str]] = {
    FlexErrorCode.STATEMENT_NOT_AVAILABLE.value: "Statement is not available.",
    FlexErrorCode.STATEMENT_INCOMPLETE.value: "Statement is incomplete at this time. Please try again shortly.",
    FlexErrorCode.SETTLEMENT_NOT_READY.value: "Settlement data is not ready at this time. Please try again shortly.",
    FlexErrorCode.FIFO_NOT_READY.value: "FIFO P/L data is not ready at this time. Please try again shortly.",
    FlexErrorCode.MTM_NOT_READY.value: "MTM P/L data is not ready at this time. Please try again shortly.",
    FlexErrorCode.MTM_AND_FIFO_NOT_READY.value: "MTM and FIFO P/L data is not ready at this time. Please try again shortly.",
    FlexErrorCode.SERVER_BUSY.value: (
        "The server is under heavy load. Statement could not be generated at this time. "
        "Please try again shortly."
    ),
    FlexErrorCode.LEGACY_QUERY_UNSUPPORTED.value: (
        "Legacy Flex Queries are no longer supported. Please convert over to Activity Flex."
    ),
    FlexErrorCode.SERVICE_ACCOUNT_INACTIVE.value: "Service account is inactive.",
    FlexErrorCode.TOKEN_EXPIRED.value: "Token has expired.",
    FlexErrorCode.IP_RESTRICTION.value: "IP restriction.",
    FlexErrorCode.INVALID_QUERY.value: "Query is invalid.",
    FlexErrorCode.INVALID_TOKEN.value: "Token is invalid.",
    FlexErrorCode.INVALID_ACCOUNT.value: "Account in invalid.",
    FlexErrorCode.INVALID_REFERENCE_CODE.value: "Reference code is invalid.",
    FlexErrorCode.RATE_LIMITED.value: "Too many requests have been made from this token. Please try again shortly.",
    FlexErrorCode.STATEMENT_IN_PROGRESS.value: "Statement generation in progress. Please try again shortly.",
    FlexErrorCode.INVALID_REQUEST.value: "Invalid request or unable to validate request.",
    FlexErrorCode.STATEMENT_UNAVAILABLE.value: "Statement could not be retrieved at this time. Please try again shortly.",
}

FLEX_RETRYABLE_POLL_CODES: Final[frozenset[str]] = frozenset(
    {
        FlexErrorCode.SERVER_BUSY.value,
        FlexErrorCode.STATEMENT_IN_PROGRESS.value,
        FlexErrorCode.RATE_LIMITED.value,
    }
)

FLEX_TOKEN_CODES: Final[frozenset[str]] = frozenset(
    {
        FlexErrorCode.TOKEN_EXPIRED.value,
        FlexErrorCode.INVALID_TOKEN.value,
    }
)

FLEX_FATAL_CODES: Final[frozenset[str]] = frozenset(
    set(FLEX_ERROR_DEFAULT_MESSAGES.keys()) - set(FLEX_RETRYABLE_POLL_CODES) - set(FLEX_TOKEN_CODES)
)


def flex_error_default_message(error_code: str, fallback_message: str) -> str:
    """Return canonical default message for an error code.

    Args:
        error_code: Upstream Flex error code.
        fallback_message: Fallback message when code is unknown.

    Returns:
        str: Canonical message for known code, else provided fallback message.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    return FLEX_ERROR_DEFAULT_MESSAGES.get(error_code, fallback_message)


def flex_error_retry_delay_seconds(error_code: str) -> int:
    """Return code-specific minimum retry delay for retryable poll errors.

    Args:
        error_code: Upstream Flex error code.

    Returns:
        int: Retry delay floor in seconds.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    if error_code == FlexErrorCode.RATE_LIMITED.value:
        return 10
    if error_code == FlexErrorCode.SERVER_BUSY.value:
        return 5
    return 5
