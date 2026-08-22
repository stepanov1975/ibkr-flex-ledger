"""Standard-library adapters for outbound operational SLO alerts."""

from email.message import EmailMessage
from hashlib import sha256
import json
import smtplib
import ssl
from typing import Literal
from urllib import request as urllib_request

from app.operations import AlertDeliveryError, AlertTransition


def _fingerprint(routing_value: str) -> str:
	return sha256(routing_value.encode("utf-8")).hexdigest()


def _display(value: object) -> str:
	if value is None:
		return "not available"
	if isinstance(value, bool):
		return str(value).lower()
	return str(value)


class WebhookAlertSender:
	"""Deliver alert transitions as JSON webhook requests."""

	channel: Literal["webhook"] = "webhook"

	def __init__(self, url: str, timeout: float = 10.0) -> None:
		normalized_url = url.strip()
		self._url = normalized_url
		self._timeout = timeout
		self.destination_fingerprint = _fingerprint(normalized_url)

	def send(self, transition: AlertTransition) -> None:
		body = json.dumps(transition.payload(), sort_keys=True).encode("utf-8")
		request = urllib_request.Request(
			self._url,
			data=body,
			headers={
				"Content-Type": "application/json",
				"Idempotency-Key": transition.event_id,
			},
			method="POST",
		)
		try:
			with urllib_request.urlopen(request, timeout=self._timeout) as response:
				status = response.status
		except Exception:
			raise AlertDeliveryError("webhook delivery failed") from None
		if not 200 <= status <= 299:
			raise AlertDeliveryError("webhook delivery failed")


class SmtpAlertSender:
	"""Deliver alert transitions as plain-text email messages."""

	channel: Literal["email"] = "email"

	def __init__(
		self,
		*,
		host: str,
		port: int,
		sender: str,
		recipients: tuple[str, ...],
		starttls: bool = True,
		username: str | None = None,
		password: str | None = None,
		timeout: float = 10.0,
	) -> None:
		self._host = host.strip()
		self._port = port
		self._sender = sender.strip()
		self._recipients = tuple(recipient.strip() for recipient in recipients)
		self._starttls = starttls
		self._username = username
		self._password = password
		self._timeout = timeout
		normalized_routing = "|".join(
			(
				self._host.casefold(),
				str(self._port),
				self._sender.casefold(),
				",".join(sorted(recipient.casefold() for recipient in self._recipients)),
			)
		)
		self.destination_fingerprint = _fingerprint(normalized_routing)

	def send(self, transition: AlertTransition) -> None:
		try:
			message = self._message(transition)
			with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
				if self._starttls:
					smtp.starttls(context=ssl.create_default_context())
				if self._username is not None and self._password is not None:
					smtp.login(self._username, self._password)
				smtp.send_message(
					message,
					from_addr=self._sender,
					to_addrs=self._recipients,
				)
		except Exception:
			raise AlertDeliveryError("email delivery failed") from None

	def _message(self, transition: AlertTransition) -> EmailMessage:
		payload = transition.payload()
		message = EmailMessage()
		message["From"] = self._sender
		message["To"] = ", ".join(self._recipients)
		message["Subject"] = f"SLO {transition.event_type} for {transition.account_id}"
		reason_codes = payload["reason_codes"]
		assert isinstance(reason_codes, list)
		message.set_content(
			"\n".join(
				(
					f"Schema version: {_display(payload['schema_version'])}",
					f"Event ID: {_display(payload['event_id'])}",
					f"Event type: {_display(payload['event_type'])}",
					f"Account: {_display(payload['account_id'])}",
					f"Measured at UTC: {_display(payload['measured_at_utc'])}",
					f"Window days: {_display(payload['window_days'])}",
					f"Run count: {_display(payload['run_count'])}",
					f"Success count: {_display(payload['success_count'])}",
					f"Success rate: {_display(payload['success_rate'])}",
					f"Success target: {_display(payload['success_target'])}",
					f"Success alert threshold: {_display(payload['success_alert_threshold'])}",
					f"P95 duration (ms): {_display(payload['p95_duration_ms'])}",
					f"P95 target (ms): {_display(payload['p95_target_ms'])}",
					f"Duration alert threshold (ms): {_display(payload['duration_alert_threshold_ms'])}",
					f"Consecutive failure alert: {_display(payload['consecutive_failure_alert'])}",
					f"Reason codes: {', '.join(reason_codes) if reason_codes else 'none'}",
				)
			)
		)
		return message
