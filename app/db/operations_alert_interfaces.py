"""Typed database contracts for outbound operations alerts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class AlertDeliveryStateRecord:
    account_id: str
    channel: str
    destination_fingerprint: str
    alerting: bool
    transition_anchor_utc: datetime
    last_delivered_at_utc: datetime | None
    updated_at_utc: datetime


class OperationsAlertDeliveryStateRepositoryPort(Protocol):
    def db_alert_delivery_state_get(
        self,
        account_id: str,
        channel: str,
        destination_fingerprint: str,
    ) -> AlertDeliveryStateRecord | None: ...

    def db_alert_delivery_state_upsert(self, record: AlertDeliveryStateRecord) -> None: ...
