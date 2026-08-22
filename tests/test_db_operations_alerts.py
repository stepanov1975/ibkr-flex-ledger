"""Unit tests for durable outbound alert delivery state persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from types import TracebackType
from typing import Literal, cast

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.db.operations_alert_interfaces import AlertDeliveryStateRecord
from app.db.operations_alerts import SQLAlchemyOperationsAlertService


class _ResultStub:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row

    def mappings(self) -> _ResultStub:
        return self

    def one_or_none(self) -> dict[str, object] | None:
        return self._row


class _ConnectionStub:
    def __init__(self, row: dict[str, object] | None = None, error: SQLAlchemyError | None = None) -> None:
        self._row = row
        self._error = error
        self.executed_queries: list[str] = []
        self.executed_parameters: list[dict[str, object]] = []

    def __enter__(self) -> _ConnectionStub:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        _ = (exc_type, exc, traceback)
        return False

    def execute(self, statement: object, parameters: dict[str, object]) -> _ResultStub:
        self.executed_queries.append(str(statement))
        self.executed_parameters.append(parameters)
        if self._error is not None:
            raise self._error
        return _ResultStub(self._row)


class _EngineStub:
    def __init__(self, connection: _ConnectionStub) -> None:
        self._connection = connection

    def connect(self) -> _ConnectionStub:
        return self._connection

    def begin(self) -> _ConnectionStub:
        return self._connection


def _record() -> AlertDeliveryStateRecord:
    now_utc = datetime(2026, 8, 22, tzinfo=timezone.utc)
    return AlertDeliveryStateRecord(
        account_id="U_TEST",
        channel="webhook",
        destination_fingerprint="a" * 64,
        alerting=True,
        transition_anchor_utc=now_utc,
        last_delivered_at_utc=now_utc,
        updated_at_utc=now_utc,
    )


def _service(connection: _ConnectionStub) -> SQLAlchemyOperationsAlertService:
    return SQLAlchemyOperationsAlertService(engine=cast(Engine, _EngineStub(connection)))


def test_alert_state_get_uses_full_channel_identity() -> None:
    """Read a delivery state only by account, channel, and destination."""

    connection = _ConnectionStub()

    assert _service(connection).db_alert_delivery_state_get("U_TEST", "webhook", "a" * 64) is None

    query = connection.executed_queries[0]
    assert "account_id=:account_id" in query
    assert "channel=:channel" in query
    assert "destination_fingerprint=:destination_fingerprint" in query
    assert connection.executed_parameters == [{
        "account_id": "U_TEST",
        "channel": "webhook",
        "destination_fingerprint": "a" * 64,
    }]


def test_alert_state_upsert_advances_transition_fields() -> None:
    """Update only mutable delivery fields when the composite state exists."""

    connection = _ConnectionStub()

    _service(connection).db_alert_delivery_state_upsert(_record())

    query = connection.executed_queries[0]
    assert "ON CONFLICT (account_id, channel, destination_fingerprint)" in query
    assert "alerting=EXCLUDED.alerting" in query
    assert "transition_anchor_utc=EXCLUDED.transition_anchor_utc" in query
    assert "last_delivered_at_utc=EXCLUDED.last_delivered_at_utc" in query
    assert "updated_at_utc=EXCLUDED.updated_at_utc" in query
    assert "account_id=EXCLUDED.account_id" not in query
    assert "channel=EXCLUDED.channel" not in query
    assert "destination_fingerprint=EXCLUDED.destination_fingerprint" not in query


@pytest.mark.parametrize("channel", ["sms", "WEBHOOK"])
def test_alert_state_rejects_unsupported_channels_before_executing_sql(channel: str) -> None:
    """Reject unsupported delivery channels before a database interaction."""

    connection = _ConnectionStub()

    with pytest.raises(ValueError, match="^unsupported alert delivery channel$"):
        _service(connection).db_alert_delivery_state_get("U_TEST", channel, "a" * 64)

    assert connection.executed_queries == []


def test_alert_state_upsert_rejects_unsupported_channel_before_executing_sql() -> None:
    """Prevent unsupported channels in records from reaching the database."""

    connection = _ConnectionStub()
    invalid_record = AlertDeliveryStateRecord(
        account_id="U_TEST",
        channel="sms",
        destination_fingerprint="a" * 64,
        alerting=True,
        transition_anchor_utc=datetime(2026, 8, 22, tzinfo=timezone.utc),
        last_delivered_at_utc=None,
        updated_at_utc=datetime(2026, 8, 22, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="^unsupported alert delivery channel$"):
        _service(connection).db_alert_delivery_state_upsert(invalid_record)

    assert connection.executed_queries == []


@pytest.mark.parametrize("operation", ["get", "upsert"])
def test_alert_state_database_failures_are_sanitized(operation: str) -> None:
    """Avoid exposing account or destination identity values in database errors."""

    connection = _ConnectionStub(error=SQLAlchemyError("database rejected U_TEST a" + "a" * 64))
    service = _service(connection)

    with pytest.raises(RuntimeError) as error:
        if operation == "get":
            service.db_alert_delivery_state_get("U_TEST", "webhook", "a" * 64)
        else:
            service.db_alert_delivery_state_upsert(_record())

    assert str(error.value) == f"alert delivery state {operation} failed"
    assert "U_TEST" not in str(error.value)
    assert "a" * 64 not in str(error.value)
