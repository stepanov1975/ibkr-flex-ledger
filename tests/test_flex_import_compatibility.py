"""Compatibility regressions for Flex request retries and report timestamps."""

from __future__ import annotations

import pytest

import app.adapters.flex_web_service as flex_module
from app.adapters.flex_errors import FlexRequestError, FlexTokenExpiredError, FlexTokenInvalidError
from app.adapters.flex_web_service import FlexWebServiceAdapter
from app.domain.flex_parsing import domain_flex_parse_timestamp_to_utc_iso


REQUEST_SUCCESS = (
    b"<FlexStatementResponse><Status>Success</Status><ReferenceCode>REF123</ReferenceCode>"
    b"<Url>https://example.test/GetStatement</Url></FlexStatementResponse>"
)
STATEMENT_SUCCESS = b'<FlexQueryResponse><FlexStatements count="1"><FlexStatement /></FlexStatements></FlexQueryResponse>'


def _failure_payload(error_code: str) -> bytes:
    return (
        "<FlexStatementResponse><Status>Fail</Status>"
        f"<ErrorCode>{error_code}</ErrorCode><ErrorMessage>Request rejected</ErrorMessage>"
        "</FlexStatementResponse>"
    ).encode()


@pytest.mark.parametrize(("error_code", "retry_floor"), [("1009", 5.0), ("1018", 10.0), ("1019", 5.0)])
def test_send_request_and_poll_have_independent_retry_budgets(
    monkeypatch: pytest.MonkeyPatch, error_code: str, retry_floor: float
) -> None:
    """Recover from transient errors in both phases with the broker's minimum wait."""

    with FlexWebServiceAdapter(
        token="test-token",
        base_url="https://example.test",
        initial_wait_seconds=0,
        retry_attempts=2,
        retry_backoff_base_seconds=1,
        jitter_min_multiplier=0.5,
        jitter_max_multiplier=0.5,
    ) as adapter:
        payloads = [_failure_payload(error_code), REQUEST_SUCCESS, _failure_payload("1019"), STATEMENT_SUCCESS]
        calls: list[tuple[str, str]] = []
        sleeps: list[float] = []

        def fake_http_get(url: str, query_parameters: dict[str, str]) -> bytes:
            calls.append((url, query_parameters["q"]))
            return payloads.pop(0)

        monkeypatch.setattr(adapter, "_adapter_http_get", fake_http_get)
        monkeypatch.setattr(flex_module.time, "sleep", sleeps.append)

        result = adapter.adapter_fetch_report(" query-id ")

    assert result.run_reference == "REF123"
    assert result.payload_bytes == STATEMENT_SUCCESS
    assert calls == [
        ("https://example.test/SendRequest", "query-id"),
        ("https://example.test/SendRequest", "query-id"),
        ("https://example.test/GetStatement", "REF123"),
        ("https://example.test/GetStatement", "REF123"),
    ]
    assert sleeps == [retry_floor, 5.0]


def test_send_request_retries_use_capped_backoff_and_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply the configured delay to retries and keep the initial wait before polling."""

    with FlexWebServiceAdapter(
        token="test-token",
        initial_wait_seconds=3,
        retry_attempts=4,
        retry_backoff_base_seconds=12,
        retry_max_backoff_seconds=20,
        random_unit_interval_provider=lambda: 0.75,
    ) as adapter:
        payloads = [_failure_payload("1009")] * 3 + [REQUEST_SUCCESS, STATEMENT_SUCCESS]
        sleeps: list[float] = []
        monkeypatch.setattr(adapter, "_adapter_http_get", lambda **_kwargs: payloads.pop(0))
        monkeypatch.setattr(flex_module.time, "sleep", sleeps.append)

        result = adapter.adapter_fetch_report("query-id")

    assert result.payload_bytes == STATEMENT_SUCCESS
    assert sleeps == [15.0, 25.0, 25.0, 3.0]


@pytest.mark.parametrize(("error_code", "retry_floor"), [("1009", 5.0), ("1018", 10.0)])
@pytest.mark.parametrize("retry_attempts", [1, 3])
def test_send_request_exhaustion_stops_at_configured_attempts(
    monkeypatch: pytest.MonkeyPatch, error_code: str, retry_floor: float, retry_attempts: int
) -> None:
    """Exhaustion retains the broker code without polling or sleeping after the final failure."""

    with FlexWebServiceAdapter(
        token="test-token", retry_attempts=retry_attempts, retry_backoff_base_seconds=0
    ) as adapter:
        calls: list[str] = []
        sleeps: list[float] = []

        def fake_http_get(url: str, query_parameters: dict[str, str]) -> bytes:
            calls.append(url.rsplit("/", 1)[-1])
            return _failure_payload(error_code)

        monkeypatch.setattr(adapter, "_adapter_http_get", fake_http_get)
        monkeypatch.setattr(flex_module.time, "sleep", sleeps.append)

        with pytest.raises(FlexRequestError) as caught:
            adapter.adapter_fetch_report("query-id")

    assert caught.value.error_code == error_code
    assert calls == ["SendRequest"] * retry_attempts
    assert sleeps == [retry_floor] * (retry_attempts - 1)


@pytest.mark.parametrize(
    ("error_code", "error_type"),
    [("1012", FlexTokenExpiredError), ("1015", FlexTokenInvalidError), ("1014", FlexRequestError), ("9999", FlexRequestError)],
)
def test_send_request_fatal_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch, error_code: str, error_type: type[FlexRequestError]
) -> None:
    """Do not consume retry waits or change error classification for fatal broker responses."""

    with FlexWebServiceAdapter(token="test-token", retry_attempts=3) as adapter:
        calls: list[str] = []
        sleeps: list[float] = []

        def fake_http_get(url: str, query_parameters: dict[str, str]) -> bytes:
            calls.append(url.rsplit("/", 1)[-1])
            return _failure_payload(error_code)

        monkeypatch.setattr(adapter, "_adapter_http_get", fake_http_get)
        monkeypatch.setattr(flex_module.time, "sleep", sleeps.append)

        with pytest.raises(error_type) as caught:
            adapter.adapter_fetch_report("query-id")

    assert caught.value.error_code == error_code
    assert calls == ["SendRequest"]
    assert sleeps == []


@pytest.mark.parametrize(
    ("timestamp", "expected_utc"),
    [
        ("2026-01-15;10:30:00 EST", "2026-01-15T15:30:00+00:00"),
        ("2026-07-15;10:30:00 EDT", "2026-07-15T14:30:00+00:00"),
        ("2026-07-15;10:30:00 EST", "2026-07-15T15:30:00+00:00"),
        ("2026-01-15;23:30:00 EST", "2026-01-16T04:30:00+00:00"),
        ("2026-01-15;103000 EST", "2026-01-15T15:30:00+00:00"),
        ("2026-01-15;10:30:00 CST", "2026-01-15T16:30:00+00:00"),
        ("2026-07-15;10:30:00 CDT", "2026-07-15T15:30:00+00:00"),
        ("2026-01-15;10:30:00 MST", "2026-01-15T17:30:00+00:00"),
        ("2026-07-15;10:30:00 MDT", "2026-07-15T16:30:00+00:00"),
        ("2026-01-15;10:30:00 PST", "2026-01-15T18:30:00+00:00"),
        ("2026-07-15;10:30:00 PDT", "2026-07-15T17:30:00+00:00"),
        ("2026-01-15;10:30:00 UTC", "2026-01-15T10:30:00+00:00"),
        ("2026-01-15;10:30:00 GMT", "2026-01-15T10:30:00+00:00"),
    ],
)
def test_recommended_flex_timestamps_preserve_explicit_timezone_offsets(timestamp: str, expected_utc: str) -> None:
    """Use each row's explicit zone when converting recommended Flex report timestamps."""

    assert domain_flex_parse_timestamp_to_utc_iso(timestamp) == expected_utc


@pytest.mark.parametrize("timestamp", ["2026-01-15;10:30:00", "2026-01-15;103000", "20260115;103000"])
def test_naive_flex_timestamps_keep_existing_utc_assumption(timestamp: str) -> None:
    assert domain_flex_parse_timestamp_to_utc_iso(timestamp) == "2026-01-15T10:30:00+00:00"


@pytest.mark.parametrize("timestamp", ["2026-01-15;10:30:00 XYZ", "2026-02-30;10:30:00 EST"])
def test_invalid_zoned_flex_timestamps_remain_unparsed(timestamp: str) -> None:
    assert domain_flex_parse_timestamp_to_utc_iso(timestamp) is None
