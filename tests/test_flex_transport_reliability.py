"""Transport reliability tests for the IBKR Flex Web Service adapter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import Callable

import httpx
import pytest

from app.adapters import FlexAdapterConnectionError, FlexAdapterTimeoutError
from app.adapters.flex_web_service import FlexWebServiceAdapter
import app.adapters.flex_web_service as flex_module


_SEND_REQUEST_SUCCESS = (
    b"<FlexStatementResponse><Status>Success</Status><ReferenceCode>REF</ReferenceCode>"
    b"<Url>https://example.test/GetStatement</Url></FlexStatementResponse>"
)
_STATEMENT = (
    b'<FlexQueryResponse><FlexStatements count="1">'
    b"<FlexStatement /></FlexStatements></FlexQueryResponse>"
)


def _adapter_with_transport(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    retry_backoff_base_seconds: float = 2,
    retry_max_backoff_seconds: float = 10,
) -> FlexWebServiceAdapter:
    adapter = FlexWebServiceAdapter(
        token="secret-token",
        base_url="https://example.test",
        initial_wait_seconds=0,
        retry_attempts=1,
        retry_backoff_base_seconds=retry_backoff_base_seconds,
        retry_max_backoff_seconds=retry_max_backoff_seconds,
        jitter_min_multiplier=1,
        jitter_max_multiplier=1,
    )
    adapter._http_client.close()
    adapter._http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return adapter


def _success_response(request: httpx.Request, payload: bytes) -> httpx.Response:
    return httpx.Response(200, content=payload, request=request)


def test_transient_503_retries_then_fetches_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient SendRequest 503 must not abort an otherwise successful fetch."""

    request_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_attempts
        if request.url.path.endswith("/SendRequest"):
            request_attempts += 1
            if request_attempts == 1:
                return httpx.Response(503, request=request)
            return _success_response(request, _SEND_REQUEST_SUCCESS)
        return _success_response(request, _STATEMENT)

    waits: list[float] = []
    monkeypatch.setattr(flex_module.time, "sleep", waits.append)

    with _adapter_with_transport(handler) as adapter:
        result = adapter.adapter_fetch_report("query")

    assert result.payload_bytes == _STATEMENT
    assert request_attempts == 2
    assert waits == [2.0]


@pytest.mark.parametrize("failure_kind", ["connect", "read", "read_timeout"])
def test_transient_transport_failure_retries_then_succeeds(
    failure_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient connection and read failures must use the transport retry budget."""

    request_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_attempts
        if request.url.path.endswith("/SendRequest"):
            request_attempts += 1
            if request_attempts == 1:
                exception_type = {
                    "connect": httpx.ConnectError,
                    "read": httpx.ReadError,
                    "read_timeout": httpx.ReadTimeout,
                }[failure_kind]
                raise exception_type(str(request.url), request=request)
            return _success_response(request, _SEND_REQUEST_SUCCESS)
        return _success_response(request, _STATEMENT)

    waits: list[float] = []
    monkeypatch.setattr(flex_module.time, "sleep", waits.append)

    with _adapter_with_transport(handler) as adapter:
        result = adapter.adapter_fetch_report("query")

    assert result.payload_bytes == _STATEMENT
    assert request_attempts == 2
    assert waits == [2.0]


def test_transient_http_status_exhaustion_uses_three_attempt_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persistently unavailable endpoint must stop after three attempts."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, request=request)

    waits: list[float] = []
    monkeypatch.setattr(flex_module.time, "sleep", waits.append)

    with _adapter_with_transport(handler) as adapter:
        with pytest.raises(FlexAdapterConnectionError, match=r"HTTP 503"):
            adapter.adapter_fetch_report("query")

    assert attempts == 3
    assert waits == [2.0, 4.0]


@pytest.mark.parametrize(
    ("failure_kind", "expected_error"),
    [
        ("connect", FlexAdapterConnectionError),
        ("read", FlexAdapterConnectionError),
        ("read_timeout", FlexAdapterTimeoutError),
    ],
)
def test_transport_failure_exhaustion_preserves_error_classification(
    failure_kind: str,
    expected_error: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausted transport retries must preserve timeout versus connection errors."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        exception_type = {
            "connect": httpx.ConnectError,
            "read": httpx.ReadError,
            "read_timeout": httpx.ReadTimeout,
        }[failure_kind]
        raise exception_type(str(request.url), request=request)

    waits: list[float] = []
    monkeypatch.setattr(flex_module.time, "sleep", waits.append)

    with _adapter_with_transport(handler) as adapter:
        with pytest.raises(expected_error):
            adapter.adapter_fetch_report("query")

    assert attempts == 3
    assert waits == [2.0, 4.0]


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_terminal_http_statuses_are_not_retried(status_code: int) -> None:
    """Client and authorization failures must remain immediate terminal failures."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, request=request)

    with _adapter_with_transport(handler) as adapter:
        with pytest.raises(FlexAdapterConnectionError, match=rf"HTTP {status_code}"):
            adapter.adapter_fetch_report("query")

    assert attempts == 1


def test_nontransient_request_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deterministic protocol failure must remain an immediate terminal error."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.UnsupportedProtocol("unsupported protocol", request=request)

    monkeypatch.setattr(flex_module.time, "sleep", lambda _seconds: None)

    with _adapter_with_transport(handler) as adapter:
        with pytest.raises(FlexAdapterConnectionError):
            adapter.adapter_fetch_report("query")

    assert attempts == 1


def test_retry_after_seconds_controls_transient_retry_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded Retry-After seconds value must delay the next attempt."""

    request_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_attempts
        if request.url.path.endswith("/SendRequest"):
            request_attempts += 1
            if request_attempts == 1:
                return httpx.Response(429, headers={"Retry-After": "7"}, request=request)
            return _success_response(request, _SEND_REQUEST_SUCCESS)
        return _success_response(request, _STATEMENT)

    waits: list[float] = []
    monkeypatch.setattr(flex_module.time, "sleep", waits.append)

    with _adapter_with_transport(handler) as adapter:
        result = adapter.adapter_fetch_report("query")

    assert result.payload_bytes == _STATEMENT
    assert waits == [7.0]


def test_retry_after_http_date_controls_transient_retry_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded Retry-After HTTP date must delay the next attempt."""

    request_attempts = 0
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=30)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_attempts
        if request.url.path.endswith("/SendRequest"):
            request_attempts += 1
            if request_attempts == 1:
                return httpx.Response(
                    503,
                    headers={"Retry-After": format_datetime(retry_at, usegmt=True)},
                    request=request,
                )
            return _success_response(request, _SEND_REQUEST_SUCCESS)
        return _success_response(request, _STATEMENT)

    waits: list[float] = []
    monkeypatch.setattr(flex_module.time, "sleep", waits.append)

    with _adapter_with_transport(handler, retry_max_backoff_seconds=60) as adapter:
        result = adapter.adapter_fetch_report("query")

    assert result.payload_bytes == _STATEMENT
    assert len(waits) == 1
    assert 28 <= waits[0] <= 30


def test_malformed_retry_after_uses_configured_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed Retry-After header must fall back to configured backoff."""

    request_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_attempts
        if request.url.path.endswith("/SendRequest"):
            request_attempts += 1
            if request_attempts == 1:
                return httpx.Response(
                    504,
                    headers={"Retry-After": "not-a-delay"},
                    request=request,
                )
            return _success_response(request, _SEND_REQUEST_SUCCESS)
        return _success_response(request, _STATEMENT)

    waits: list[float] = []
    monkeypatch.setattr(flex_module.time, "sleep", waits.append)

    with _adapter_with_transport(handler) as adapter:
        result = adapter.adapter_fetch_report("query")

    assert result.payload_bytes == _STATEMENT
    assert waits == [2.0]


def test_excessive_retry_after_fails_without_sleeping_or_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry-After beyond the configured cap must fail instead of retrying early."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, headers={"Retry-After": "11"}, request=request)

    waits: list[float] = []
    monkeypatch.setattr(flex_module.time, "sleep", waits.append)

    with _adapter_with_transport(handler, retry_max_backoff_seconds=10) as adapter:
        with pytest.raises(
            FlexAdapterConnectionError,
            match=r"Retry-After 11\.0 seconds exceeds configured maximum 10\.0 seconds",
        ):
            adapter.adapter_fetch_report("query")

    assert attempts == 1
    assert waits == []


def test_statement_retry_reuses_fetched_reference_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """A retried GetStatement call must reuse the reference returned by SendRequest."""

    requested_statement_reference_codes: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/SendRequest"):
            return _success_response(request, _SEND_REQUEST_SUCCESS)
        requested_statement_reference_codes.append(request.url.params.get("q"))
        if len(requested_statement_reference_codes) == 1:
            return httpx.Response(503, request=request)
        return _success_response(request, _STATEMENT)

    monkeypatch.setattr(flex_module.time, "sleep", lambda _seconds: None)

    with _adapter_with_transport(handler) as adapter:
        result = adapter.adapter_fetch_report("query")

    assert result.payload_bytes == _STATEMENT
    assert requested_statement_reference_codes == ["REF", "REF"]


def test_transport_errors_do_not_expose_flex_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transport failures must not copy the token-bearing request URL into errors."""

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError(str(request.url), request=request)

    monkeypatch.setattr(flex_module.time, "sleep", lambda _seconds: None)

    with _adapter_with_transport(handler) as adapter:
        with pytest.raises(FlexAdapterConnectionError) as caught:
            adapter.adapter_fetch_report("query")

    assert attempts == 3
    assert "secret-token" not in str(caught.value)
    assert "https://" not in str(caught.value)
