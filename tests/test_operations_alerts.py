from datetime import datetime, timedelta, timezone
from typing import Literal

from app.db import AlertDeliveryStateRecord
from app.operations import (
    AlertTransition,
    OperationsSloStatus,
    operations_build_slo_status,
    operations_evaluate_slo_alerts,
)


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=15)
RECOVERY_TIME = LATER + timedelta(minutes=15)
SECOND_ALERT_TIME = RECOVERY_TIME + timedelta(minutes=15)


class InMemoryAlertDeliveryStateRepository:
    def __init__(self, failing_fingerprints: set[str] | None = None) -> None:
        self.states: dict[tuple[str, str, str], AlertDeliveryStateRecord] = {}
        self.failing_fingerprints = failing_fingerprints or set()

    def db_alert_delivery_state_get(
        self,
        account_id: str,
        channel: str,
        destination_fingerprint: str,
    ) -> AlertDeliveryStateRecord | None:
        return self.states.get((account_id, channel, destination_fingerprint))

    def db_alert_delivery_state_upsert(self, record: AlertDeliveryStateRecord) -> None:
        if record.destination_fingerprint in self.failing_fingerprints:
            raise RuntimeError("secret persistence failure")
        key = (record.account_id, record.channel, record.destination_fingerprint)
        self.states[key] = record

    def only_state(self) -> AlertDeliveryStateRecord:
        assert len(self.states) == 1
        return next(iter(self.states.values()))


class RecordingAlertSender:
    channel: Literal["webhook", "email"]

    def __init__(
        self,
        channel: Literal["webhook", "email"],
        destination_fingerprint: str,
        failures_remaining: int = 0,
    ) -> None:
        self.channel = channel
        self.destination_fingerprint = destination_fingerprint
        self.failures_remaining = failures_remaining
        self.attempted: list[AlertTransition] = []
        self.transitions: list[AlertTransition] = []

    def send(self, transition: AlertTransition) -> None:
        self.attempted.append(transition)
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("secret delivery failure")
        self.transitions.append(transition)


def _healthy_status(measured_at_utc: datetime = NOW) -> OperationsSloStatus:
    return operations_build_slo_status([], measured_at_utc)


def _alerting_status(measured_at_utc: datetime = NOW) -> OperationsSloStatus:
    healthy = operations_build_slo_status([], measured_at_utc)
    return OperationsSloStatus(
        measured_at_utc=measured_at_utc,
        summary=healthy.summary,
        reason_codes=("consecutive_failures",),
    )


def test_initial_healthy_state_records_baseline_without_delivery() -> None:
    repository = InMemoryAlertDeliveryStateRepository()
    webhook = RecordingAlertSender("webhook", "webhook-fingerprint")

    result = operations_evaluate_slo_alerts(
        "U_TEST", _healthy_status(), repository, [webhook], NOW
    )

    assert result.delivered_channels == ()
    assert result.failed_channels == ()
    assert webhook.transitions == []
    assert repository.only_state().alerting is False
    assert repository.only_state().last_delivered_at_utc is None


def test_alert_is_sent_once_then_recovery_is_sent_once() -> None:
    repository = InMemoryAlertDeliveryStateRepository()
    webhook = RecordingAlertSender("webhook", "webhook-fingerprint")

    first = operations_evaluate_slo_alerts(
        "U_TEST", _alerting_status(), repository, [webhook], NOW
    )
    duplicate = operations_evaluate_slo_alerts(
        "U_TEST", _alerting_status(LATER), repository, [webhook], LATER
    )
    recovery = operations_evaluate_slo_alerts(
        "U_TEST", _healthy_status(RECOVERY_TIME), repository, [webhook], RECOVERY_TIME
    )

    assert first.delivered_channels == ("webhook",)
    assert duplicate.delivered_channels == ()
    assert recovery.delivered_channels == ("webhook",)
    assert [item.event_type for item in webhook.transitions] == ["alert", "recovery"]


def test_failed_channel_retries_stable_event_while_successful_channel_stays_deduplicated() -> None:
    repository = InMemoryAlertDeliveryStateRepository()
    webhook = RecordingAlertSender("webhook", "webhook-fingerprint")
    email = RecordingAlertSender("email", "email-fingerprint", failures_remaining=1)

    result = operations_evaluate_slo_alerts(
        "U_TEST", _alerting_status(), repository, [webhook, email], NOW
    )
    first_email_event_id = email.attempted[0].event_id
    retry = operations_evaluate_slo_alerts(
        "U_TEST", _alerting_status(LATER), repository, [webhook, email], LATER
    )

    assert result.delivered_channels == ("webhook",)
    assert result.failed_channels == ("email",)
    assert retry.delivered_channels == ("email",)
    assert retry.failed_channels == ()
    assert len(webhook.transitions) == 1
    assert email.attempted[1].event_id == first_email_event_id


def test_later_alert_episode_uses_a_different_event_id() -> None:
    repository = InMemoryAlertDeliveryStateRepository()
    webhook = RecordingAlertSender("webhook", "webhook-fingerprint")

    operations_evaluate_slo_alerts(
        "U_TEST", _alerting_status(), repository, [webhook], NOW
    )
    operations_evaluate_slo_alerts(
        "U_TEST", _healthy_status(RECOVERY_TIME), repository, [webhook], RECOVERY_TIME
    )
    operations_evaluate_slo_alerts(
        "U_TEST",
        _alerting_status(SECOND_ALERT_TIME),
        repository,
        [webhook],
        SECOND_ALERT_TIME,
    )

    first_alert, _, second_alert = webhook.transitions
    assert second_alert.event_id != first_alert.event_id


def test_persistence_failure_is_sanitized_and_does_not_stop_other_channels() -> None:
    repository = InMemoryAlertDeliveryStateRepository({"webhook-fingerprint"})
    webhook = RecordingAlertSender("webhook", "webhook-fingerprint")
    email = RecordingAlertSender("email", "email-fingerprint")

    result = operations_evaluate_slo_alerts(
        "U_TEST", _alerting_status(), repository, [webhook, email], NOW
    )
    first_webhook_event_id = webhook.attempted[0].event_id
    repository.failing_fingerprints.clear()
    retry = operations_evaluate_slo_alerts(
        "U_TEST", _alerting_status(LATER), repository, [webhook, email], LATER
    )

    assert result.delivered_channels == ("email",)
    assert result.failed_channels == ("webhook",)
    assert retry.delivered_channels == ("webhook",)
    assert retry.failed_channels == ()
    assert webhook.attempted[1].event_id == first_webhook_event_id
    assert len(email.transitions) == 1
    assert email.transitions[0].event_type == "alert"
