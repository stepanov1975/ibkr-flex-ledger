"""Regression tests for Flex Web Service adapter error handling and retry behavior."""

from __future__ import annotations

from urllib.error import URLError

import pytest

from app.adapters.flex_web_service import FlexWebServiceAdapter
import app.adapters.flex_web_service as flex_module


def test_adapters_flex_http_timeout_reason_raises_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raise TimeoutError when transport layer reports timeout reason.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None: Assertions validate timeout mapping behavior.

    Raises:
        AssertionError: Raised when timeout mapping is incorrect.
    """

    adapter = FlexWebServiceAdapter(token="token")

    def _raise_timeout(_request, timeout=None):
        _ = timeout
        raise URLError(TimeoutError("timed out"))

    monkeypatch.setattr(flex_module, "urlopen", _raise_timeout)

    with pytest.raises(TimeoutError, match="timed out"):
        adapter.adapter_fetch_report(query_id="query-id")


def test_adapters_flex_poll_retries_on_throttled_error_code_1018(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry polling when upstream returns throttled code 1018.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None: Assertions validate retry behavior and successful payload return.

    Raises:
        AssertionError: Raised when retry behavior is not respected.
    """

    adapter = FlexWebServiceAdapter(
        token="token",
        initial_wait_seconds=0,
        retry_attempts=2,
        retry_increment_seconds=0,
    )

    request_success_payload = (
        b"<FlexStatementResponse><Status>Success</Status><ReferenceCode>REF123</ReferenceCode>"
        b"<Url>https://example.test/GetStatement</Url></FlexStatementResponse>"
    )
    throttled_payload = (
        b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>1018</ErrorCode>"
        b"<ErrorMessage>Too many requests</ErrorMessage></FlexStatementResponse>"
    )
    success_payload = b"<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement /></FlexStatements></FlexQueryResponse>"
    payload_sequence = [request_success_payload, throttled_payload, success_payload]
    sleep_calls: list[float] = []

    def _fake_http_get(url: str, query_parameters: dict[str, str]) -> bytes:
        _ = (url, query_parameters)
        return payload_sequence.pop(0)

    def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(adapter, "_adapter_http_get", _fake_http_get)
    monkeypatch.setattr(flex_module.time, "sleep", _fake_sleep)

    result = adapter.adapter_fetch_report(query_id="query-id")

    assert result.payload_bytes == success_payload
    assert any(seconds >= 10 for seconds in sleep_calls)
