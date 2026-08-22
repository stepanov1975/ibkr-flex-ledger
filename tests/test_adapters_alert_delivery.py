import json
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

import pytest

from app.adapters import SmtpAlertSender, WebhookAlertSender
from app.analytics import IngestionSloSummary
from app.operations import AlertDeliveryError, AlertTransition, OperationsSloStatus


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _transition(event_type: str = "alert") -> AlertTransition:
	status = OperationsSloStatus(
		measured_at_utc=NOW,
		summary=IngestionSloSummary(
			run_count=20,
			success_count=18,
			success_rate=0.9,
			success_target=0.99,
			success_alert_threshold=0.98,
			success_breached=event_type == "alert",
			p95_duration_ms=2_100_000,
			p95_target_ms=900_000,
			duration_alert_threshold_ms=1_800_000,
			duration_breached=event_type == "alert",
			consecutive_failure_alert=event_type == "alert",
		),
		reason_codes=("success_rate_below_threshold", "duration_above_threshold")
		if event_type == "alert"
		else (),
	)
	return AlertTransition(
		event_id="event-123",
		event_type="alert" if event_type == "alert" else "recovery",
		account_id="U_TEST",
		status=status,
	)


class _WebhookResponse:
	def __init__(self, status: int) -> None:
		self.status = status

	def __enter__(self) -> "_WebhookResponse":
		return self

	def __exit__(self, *args: object) -> None:
		return None


def test_webhook_posts_exact_transition_payload_with_idempotency_key(monkeypatch) -> None:
	captured: dict[str, Any] = {}

	def urlopen(request: urllib_request.Request, timeout: float) -> _WebhookResponse:
		captured["request"] = request
		captured["timeout"] = timeout
		return _WebhookResponse(204)

	monkeypatch.setattr(urllib_request, "urlopen", urlopen)
	transition = _transition()
	sender = WebhookAlertSender("https://hooks.example.test/secret")

	sender.send(transition)

	request = captured["request"]
	assert request.full_url == "https://hooks.example.test/secret"
	assert request.get_method() == "POST"
	assert request.get_header("Content-type") == "application/json"
	assert request.get_header("Idempotency-key") == transition.event_id
	assert json.loads(request.data) == transition.payload()
	assert request.data == json.dumps(transition.payload(), sort_keys=True).encode("utf-8")
	assert set(json.loads(request.data)) == {
		"schema_version",
		"event_id",
		"event_type",
		"account_id",
		"measured_at_utc",
		"window_days",
		"run_count",
		"success_count",
		"success_rate",
		"success_target",
		"success_alert_threshold",
		"p95_duration_ms",
		"p95_target_ms",
		"duration_alert_threshold_ms",
		"consecutive_failure_alert",
		"reason_codes",
	}
	assert captured["timeout"] == 10.0
	assert "hooks.example.test" not in sender.destination_fingerprint
	assert len(sender.destination_fingerprint) == 64
	assert "hooks.example.test" not in repr(sender)


def test_webhook_rejects_non_success_response_without_exposing_destination(monkeypatch) -> None:
	monkeypatch.setattr(
		urllib_request,
		"urlopen",
		lambda request, timeout: _WebhookResponse(500),
	)
	sender = WebhookAlertSender("https://hooks.example.test/secret")

	with pytest.raises(AlertDeliveryError, match="^webhook delivery failed$") as raised:
		sender.send(_transition())

	assert "hooks.example.test" not in str(raised.value)
	assert raised.value.__cause__ is None


def test_webhook_sanitizes_transport_failure_without_chaining(monkeypatch) -> None:
	def fail_urlopen(request: urllib_request.Request, timeout: float) -> None:
		raise urllib_error.URLError("hooks.example.test/secret")

	monkeypatch.setattr(urllib_request, "urlopen", fail_urlopen)
	sender = WebhookAlertSender("https://hooks.example.test/secret")

	with pytest.raises(AlertDeliveryError, match="^webhook delivery failed$") as raised:
		sender.send(_transition())

	assert "hooks.example.test" not in str(raised.value)
	assert raised.value.__cause__ is None
	assert raised.value.__suppress_context__ is True


class _RecordingSmtp:
	instances: list["_RecordingSmtp"] = []

	def __init__(self, host: str, port: int, *, timeout: float) -> None:
		self.host = host
		self.port = port
		self.timeout = timeout
		self.calls: list[tuple[object, ...]] = []
		self.messages: list[EmailMessage] = []
		self.__class__.instances.append(self)

	def __enter__(self) -> "_RecordingSmtp":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def starttls(self) -> None:
		self.calls.append(("starttls",))

	def login(self, username: str, password: str) -> None:
		self.calls.append(("login", username, password))

	def send_message(
		self,
		message: EmailMessage,
		from_addr: str,
		to_addrs: tuple[str, ...],
	) -> None:
		self.calls.append(("send_message", from_addr, to_addrs))
		self.messages.append(message)


def test_smtp_uses_tls_authentication_and_renders_alert(monkeypatch) -> None:
	_RecordingSmtp.instances.clear()
	monkeypatch.setattr(smtplib, "SMTP", _RecordingSmtp)
	sender = SmtpAlertSender(
		host="smtp.example.test",
		port=2525,
		sender="alerts@example.test",
		recipients=("owner@example.test", "oncall@example.test"),
		starttls=True,
		username="smtp-user",
		password="smtp-secret",
		timeout=12.5,
	)

	sender.send(_transition())

	smtp = _RecordingSmtp.instances[0]
	assert (smtp.host, smtp.port, smtp.timeout) == ("smtp.example.test", 2525, 12.5)
	assert smtp.calls == [
		("starttls",),
		("login", "smtp-user", "smtp-secret"),
		(
			"send_message",
			"alerts@example.test",
			("owner@example.test", "oncall@example.test"),
		),
	]
	message = smtp.messages[0]
	assert message["From"] == "alerts@example.test"
	assert message["To"] == "owner@example.test, oncall@example.test"
	assert message["Subject"] == "SLO alert for U_TEST"
	body = message.get_content()
	for expected in (
		"Event ID: event-123",
		"Account: U_TEST",
		"Reason codes: success_rate_below_threshold, duration_above_threshold",
		"Run count: 20",
		"Success count: 18",
		"Success rate: 0.9",
		"Success alert threshold: 0.98",
		"P95 duration (ms): 2100000",
		"Duration alert threshold (ms): 1800000",
		"Consecutive failure alert: true",
	):
		assert expected in body
	assert len(sender.destination_fingerprint) == 64
	for routing_value in (
		"smtp.example.test",
		"alerts@example.test",
		"owner@example.test",
		"smtp-user",
		"smtp-secret",
	):
		assert routing_value not in sender.destination_fingerprint
		assert routing_value not in repr(sender)


def test_smtp_can_skip_tls_and_authentication_and_renders_recovery(monkeypatch) -> None:
	_RecordingSmtp.instances.clear()
	monkeypatch.setattr(smtplib, "SMTP", _RecordingSmtp)
	sender = SmtpAlertSender(
		host="smtp.example.test",
		port=25,
		sender="alerts@example.test",
		recipients=("owner@example.test",),
		starttls=False,
		timeout=10.0,
	)

	sender.send(_transition("recovery"))

	smtp = _RecordingSmtp.instances[0]
	assert smtp.calls == [
		("send_message", "alerts@example.test", ("owner@example.test",)),
	]
	message = smtp.messages[0]
	assert message["Subject"] == "SLO recovery for U_TEST"
	assert "Event type: recovery" in message.get_content()
	assert "Reason codes: none" in message.get_content()


def test_smtp_sanitizes_delivery_failure_without_chaining(monkeypatch) -> None:
	class FailingSmtp:
		def __init__(self, host: str, port: int, *, timeout: float) -> None:
			raise smtplib.SMTPException("smtp.example.test owner@example.test")

	monkeypatch.setattr(smtplib, "SMTP", FailingSmtp)
	sender = SmtpAlertSender(
		host="smtp.example.test",
		port=25,
		sender="alerts@example.test",
		recipients=("owner@example.test",),
		starttls=False,
		timeout=10.0,
	)

	with pytest.raises(AlertDeliveryError, match="^email delivery failed$") as raised:
		sender.send(_transition())

	assert "smtp.example.test" not in str(raised.value)
	assert "owner@example.test" not in str(raised.value)
	assert raised.value.__cause__ is None
	assert raised.value.__suppress_context__ is True


def test_smtp_sanitizes_message_construction_failure_without_exposing_routing() -> None:
	sender = SmtpAlertSender(
		host="smtp.example.test",
		port=25,
		sender="alerts@example.test\nsecret-routing-value",
		recipients=("owner@example.test",),
		starttls=False,
	)

	with pytest.raises(AlertDeliveryError, match="^email delivery failed$") as raised:
		sender.send(_transition())

	assert "secret-routing-value" not in str(raised.value)
	assert raised.value.__cause__ is None
	assert raised.value.__suppress_context__ is True
