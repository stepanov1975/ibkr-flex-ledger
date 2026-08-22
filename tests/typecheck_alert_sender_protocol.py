"""Static contract checks for concrete outbound alert senders."""

from app.adapters import SmtpAlertSender, WebhookAlertSender
from app.operations import AlertSenderPort


def _requires_alert_sender(sender: AlertSenderPort) -> None:
    pass


_requires_alert_sender(WebhookAlertSender("https://hooks.example.test/path"))
_requires_alert_sender(
    SmtpAlertSender(
        host="smtp.example.test",
        port=587,
        sender="alerts@example.test",
        recipients=("owner@example.test",),
    )
)
