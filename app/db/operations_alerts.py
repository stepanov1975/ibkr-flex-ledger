"""PostgreSQL persistence for outbound operations alert delivery state."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from .operations_alert_interfaces import AlertDeliveryStateRecord, OperationsAlertDeliveryStateRepositoryPort


class SQLAlchemyOperationsAlertService(OperationsAlertDeliveryStateRepositoryPort):
    """Database implementation for per-channel outbound alert state."""

    _SUPPORTED_CHANNELS = frozenset({"webhook", "email"})

    def __init__(self, engine: Engine):
        if engine is None:
            raise ValueError("engine must not be None")
        self._engine = engine

    def db_alert_delivery_state_get(
        self,
        account_id: str,
        channel: str,
        destination_fingerprint: str,
    ) -> AlertDeliveryStateRecord | None:
        self._validate_channel(channel)
        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT account_id, channel, destination_fingerprint, alerting, transition_anchor_utc, "
                        "last_delivered_at_utc, updated_at_utc FROM alert_delivery_state "
                        "WHERE account_id=:account_id AND channel=:channel "
                        "AND destination_fingerprint=:destination_fingerprint"
                    ),
                    {
                        "account_id": account_id,
                        "channel": channel,
                        "destination_fingerprint": destination_fingerprint,
                    },
                ).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise RuntimeError("alert delivery state get failed") from error
        return None if row is None else self._record(row)

    def db_alert_delivery_state_upsert(self, record: AlertDeliveryStateRecord) -> None:
        self._validate_channel(record.channel)
        try:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO alert_delivery_state ("
                        "account_id, channel, destination_fingerprint, alerting, transition_anchor_utc, "
                        "last_delivered_at_utc, updated_at_utc"
                        ") VALUES ("
                        ":account_id, :channel, :destination_fingerprint, :alerting, :transition_anchor_utc, "
                        ":last_delivered_at_utc, :updated_at_utc"
                        ") ON CONFLICT (account_id, channel, destination_fingerprint) DO UPDATE SET "
                        "alerting=EXCLUDED.alerting, transition_anchor_utc=EXCLUDED.transition_anchor_utc, "
                        "last_delivered_at_utc=EXCLUDED.last_delivered_at_utc, updated_at_utc=EXCLUDED.updated_at_utc"
                    ),
                    {
                        "account_id": record.account_id,
                        "channel": record.channel,
                        "destination_fingerprint": record.destination_fingerprint,
                        "alerting": record.alerting,
                        "transition_anchor_utc": record.transition_anchor_utc,
                        "last_delivered_at_utc": record.last_delivered_at_utc,
                        "updated_at_utc": record.updated_at_utc,
                    },
                )
        except SQLAlchemyError as error:
            raise RuntimeError("alert delivery state upsert failed") from error

    @classmethod
    def _validate_channel(cls, channel: str) -> None:
        if channel not in cls._SUPPORTED_CHANNELS:
            raise ValueError("unsupported alert delivery channel")

    @staticmethod
    def _record(row: Any) -> AlertDeliveryStateRecord:
        return AlertDeliveryStateRecord(
            account_id=row["account_id"],
            channel=row["channel"],
            destination_fingerprint=row["destination_fingerprint"],
            alerting=row["alerting"],
            transition_anchor_utc=row["transition_anchor_utc"],
            last_delivered_at_utc=row["last_delivered_at_utc"],
            updated_at_utc=row["updated_at_utc"],
        )
