"""Project-native typed exceptions for IBKR Flex adapter failures."""

from __future__ import annotations


class FlexAdapterError(Exception):
    """Base exception for adapter-level Flex failures.

    Attributes:
        error_code: Optional upstream Flex error code.
    """

    def __init__(self, message: str, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code


class FlexAdapterConnectionError(FlexAdapterError, ConnectionError):
    """Transport-level connectivity failure during Flex API communication."""


class FlexAdapterTimeoutError(FlexAdapterError, TimeoutError):
    """Transport or polling timeout while waiting for Flex API response."""


class FlexRequestError(FlexAdapterError, ValueError):
    """Request-phase contract failure during SendRequest handling."""


class FlexStatementError(FlexAdapterError, RuntimeError):
    """Statement-phase failure during GetStatement polling or parsing."""


class FlexRetryableStatementError(FlexStatementError):
    """Retryable statement-phase condition surfaced by Flex error codes."""

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        retry_after_seconds: float | None = None,
    ):
        super().__init__(message=message, error_code=error_code)
        self.retry_after_seconds = retry_after_seconds


class FlexTokenError(FlexRequestError):
    """Token lifecycle failure detected during request processing."""


class FlexTokenExpiredError(FlexTokenError):
    """Expired token failure (`1012`) detected during request processing."""


class FlexTokenInvalidError(FlexTokenError):
    """Invalid token failure (`1015`) detected during request processing."""
