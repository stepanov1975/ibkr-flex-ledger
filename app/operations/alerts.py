"""Pure transition evaluation for outbound operational SLO alerts."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal, Protocol, Sequence

from app.db import AlertDeliveryStateRecord, AlertDeliveryStateRepositoryPort

from .slo_status import OperationsSloStatus


class AlertDeliveryError(RuntimeError):
    """Sanitized outbound alert delivery failure."""


@dataclass(frozen=True)
class AlertTransition:
    event_id: str
    event_type: Literal["alert", "recovery"]
    account_id: str
    status: OperationsSloStatus

    def payload(self) -> dict[str, object]:
        summary = self.status.summary
        return {
            "schema_version": "1",
            "event_id": self.event_id,
            "event_type": self.event_type,
            "account_id": self.account_id,
            "measured_at_utc": self.status.measured_at_utc.isoformat(),
            "window_days": 30,
            "run_count": summary.run_count,
            "success_count": summary.success_count,
            "success_rate": summary.success_rate,
            "success_target": summary.success_target,
            "success_alert_threshold": summary.success_alert_threshold,
            "p95_duration_ms": summary.p95_duration_ms,
            "p95_target_ms": summary.p95_target_ms,
            "duration_alert_threshold_ms": summary.duration_alert_threshold_ms,
            "consecutive_failure_alert": summary.consecutive_failure_alert,
            "reason_codes": list(self.status.reason_codes),
        }


class AlertSenderPort(Protocol):
    channel: Literal["webhook", "email"]
    destination_fingerprint: str

    def send(self, transition: AlertTransition) -> None: ...


@dataclass(frozen=True)
class AlertEvaluationResult:
    delivered_channels: tuple[str, ...]
    failed_channels: tuple[str, ...]


def operations_evaluate_slo_alerts(
    account_id: str,
    status: OperationsSloStatus,
    state_repository: AlertDeliveryStateRepositoryPort,
    senders: Sequence[AlertSenderPort],
    evaluated_at_utc: datetime,
) -> AlertEvaluationResult:
    delivered_channels: list[str] = []
    failed_channels: list[str] = []

    for sender in senders:
        try:
            state = state_repository.db_alert_delivery_state_get(
                account_id,
                sender.channel,
                sender.destination_fingerprint,
            )
            if state is None and not status.alerting:
                state_repository.db_alert_delivery_state_upsert(
                    AlertDeliveryStateRecord(
                        account_id=account_id,
                        channel=sender.channel,
                        destination_fingerprint=sender.destination_fingerprint,
                        alerting=False,
                        transition_anchor_utc=evaluated_at_utc,
                        last_delivered_at_utc=None,
                        updated_at_utc=evaluated_at_utc,
                    )
                )
                continue
            if state is not None and state.alerting == status.alerting:
                continue

            event_type: Literal["alert", "recovery"] = (
                "alert" if status.alerting else "recovery"
            )
            transition = AlertTransition(
                event_id=_event_id(
                    account_id,
                    sender.channel,
                    sender.destination_fingerprint,
                    status.alerting,
                    state.transition_anchor_utc.isoformat() if state else "initial",
                ),
                event_type=event_type,
                account_id=account_id,
                status=status,
            )
            sender.send(transition)
            state_repository.db_alert_delivery_state_upsert(
                AlertDeliveryStateRecord(
                    account_id=account_id,
                    channel=sender.channel,
                    destination_fingerprint=sender.destination_fingerprint,
                    alerting=status.alerting,
                    transition_anchor_utc=evaluated_at_utc,
                    last_delivered_at_utc=evaluated_at_utc,
                    updated_at_utc=evaluated_at_utc,
                )
            )
            delivered_channels.append(sender.channel)
        except Exception:
            failed_channels.append(sender.channel)

    return AlertEvaluationResult(tuple(delivered_channels), tuple(failed_channels))


def _event_id(
    account_id: str,
    channel: str,
    destination_fingerprint: str,
    alerting: bool,
    transition_anchor: str,
) -> str:
    target_state = "alerting" if alerting else "healthy"
    event_identity = "|".join(
        (
            account_id,
            channel,
            destination_fingerprint,
            target_state,
            transition_anchor,
        )
    )
    return sha256(event_identity.encode("utf-8")).hexdigest()
