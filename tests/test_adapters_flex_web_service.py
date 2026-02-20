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
        retry_backoff_base_seconds=0,
        retry_max_backoff_seconds=10,
        jitter_min_multiplier=1.0,
        jitter_max_multiplier=1.0,
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


def test_adapters_flex_poll_retries_on_server_busy_error_code_1009(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry polling when upstream returns server-busy code 1009.

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
        retry_backoff_base_seconds=0,
        retry_max_backoff_seconds=10,
        jitter_min_multiplier=1.0,
        jitter_max_multiplier=1.0,
    )

    request_success_payload = (
        b"<FlexStatementResponse><Status>Success</Status><ReferenceCode>REF123</ReferenceCode>"
        b"<Url>https://example.test/GetStatement</Url></FlexStatementResponse>"
    )
    server_busy_payload = (
        b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>1009</ErrorCode>"
        b"<ErrorMessage>Server busy</ErrorMessage></FlexStatementResponse>"
    )
    success_payload = b"<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement /></FlexStatements></FlexQueryResponse>"
    payload_sequence = [request_success_payload, server_busy_payload, success_payload]
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
    assert any(seconds >= 5 for seconds in sleep_calls)


def test_adapters_flex_request_known_error_code_uses_fallback_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use deterministic fallback message for known code when upstream message is blank.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        None: Assertions validate deterministic known-code fallback behavior.

    Raises:
        AssertionError: Raised when request rejection message is not normalized.
    """

    adapter = FlexWebServiceAdapter(token="token")
    request_failed_payload = (
        b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>1012</ErrorCode>"
        b"<ErrorMessage></ErrorMessage></FlexStatementResponse>"
    )

    def _fake_http_get(url: str, query_parameters: dict[str, str]) -> bytes:
        _ = (url, query_parameters)
        return request_failed_payload

    monkeypatch.setattr(adapter, "_adapter_http_get", _fake_http_get)

    with pytest.raises(ValueError, match=r"code=1012, message=Token has expired\."):
        adapter.adapter_fetch_report(query_id="query-id")


def test_adapters_flex_retry_wait_uses_exponential_backoff_with_cap_and_jitter() -> None:
    """Calculate exponential backoff wait with deterministic jitter and cap.

    Args:
        None: This test uses deterministic adapter configuration only.

    Returns:
        None: Assertions verify wait-seconds calculation contract.

    Raises:
        AssertionError: Raised when computed wait values are incorrect.
    """

    adapter = FlexWebServiceAdapter(
        token="token",
        initial_wait_seconds=0,
        retry_backoff_base_seconds=4,
        retry_max_backoff_seconds=10,
        jitter_min_multiplier=1.0,
        jitter_max_multiplier=1.0,
        random_unit_interval_provider=lambda: 0.0,
    )

    assert adapter._adapter_calculate_retry_wait_seconds(retry_index=0) == pytest.approx(4.0)
    assert adapter._adapter_calculate_retry_wait_seconds(retry_index=1) == pytest.approx(8.0)
    assert adapter._adapter_calculate_retry_wait_seconds(retry_index=2) == pytest.approx(10.0)


def test_adapters_flex_retry_wait_respects_initial_wait_floor() -> None:
    """Return initial wait when jittered backoff is lower than configured initial floor.

    Args:
        None: This test uses deterministic adapter configuration only.

    Returns:
        None: Assertions verify initial wait floor behavior.

    Raises:
        AssertionError: Raised when initial wait floor is not applied.
    """

    adapter = FlexWebServiceAdapter(
        token="token",
        initial_wait_seconds=5,
        retry_backoff_base_seconds=1,
        retry_max_backoff_seconds=60,
        jitter_min_multiplier=0.5,
        jitter_max_multiplier=0.5,
        random_unit_interval_provider=lambda: 0.0,
    )

    assert adapter._adapter_calculate_retry_wait_seconds(retry_index=0) == pytest.approx(5.0)
