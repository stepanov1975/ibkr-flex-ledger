import pytest
from pydantic import SecretStr, ValidationError

from app.config import AppSettings


BASE = {"ibkr_flex_token": "token", "ibkr_flex_query_id": "query"}


def test_alert_channels_are_disabled_by_default() -> None:
    settings = AppSettings(**BASE)

    assert settings.alert_webhook_url is None
    assert settings.alert_smtp_host is None
    assert settings.alert_email_recipients() == ()


def test_complete_email_configuration_parses_recipients() -> None:
    settings = AppSettings(
        **BASE,
        alert_smtp_host="smtp.example.test",
        alert_smtp_username="user",
        alert_smtp_password="secret",
        alert_email_from="alerts@example.test",
        alert_email_to="one@example.test, two@example.test",
    )

    assert settings.alert_email_recipients() == (
        "one@example.test",
        "two@example.test",
    )


@pytest.mark.parametrize(
    "override",
    [
        {"alert_webhook_url": "ftp://example.test/hook"},
        {"alert_smtp_host": "smtp.example.test"},
        {"alert_smtp_username": "user"},
        {"alert_smtp_password": "secret"},
    ],
)
def test_partial_or_unsafe_alert_configuration_is_rejected(override: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        AppSettings(**BASE, **override)


def test_blank_alert_environment_values_disable_delivery() -> None:
    settings = AppSettings(
        **BASE,
        alert_webhook_url="  ",
        alert_smtp_host="  ",
        alert_smtp_username="  ",
        alert_smtp_password="  ",
        alert_email_from="  ",
        alert_email_to="  ",
    )

    assert settings.alert_webhook_url is None
    assert settings.alert_smtp_host is None
    assert settings.alert_smtp_username is None
    assert settings.alert_smtp_password is None
    assert settings.alert_email_from is None
    assert settings.alert_email_to is None


def test_alert_secrets_and_destinations_are_hidden_from_repr() -> None:
    settings = AppSettings(
        **BASE,
        alert_webhook_url="https://hooks.example.test/secret",
        alert_smtp_host="smtp.example.test",
        alert_smtp_username="user",
        alert_smtp_password="secret",
        alert_email_from="alerts@example.test",
        alert_email_to="one@example.test",
    )

    settings_repr = repr(settings)
    assert isinstance(settings.alert_webhook_url, SecretStr)
    assert isinstance(settings.alert_smtp_password, SecretStr)
    assert "hooks.example.test" not in settings_repr
    assert "smtp.example.test" not in settings_repr
    assert "alerts@example.test" not in settings_repr
    assert "one@example.test" not in settings_repr
    assert "user" not in settings_repr
    assert "secret" not in settings_repr


@pytest.mark.parametrize("field,value", [("alert_smtp_port", 0), ("alert_smtp_port", 65_536), ("alert_delivery_timeout_seconds", 0), ("alert_delivery_timeout_seconds", 121)])
def test_alert_delivery_bounds_are_validated(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        AppSettings(**BASE, **{field: value})


def test_blank_recipient_entries_are_removed() -> None:
    settings = AppSettings(
        **BASE,
        alert_smtp_host="smtp.example.test",
        alert_email_from="alerts@example.test",
        alert_email_to=" one@example.test, , two@example.test, ",
    )

    assert settings.alert_email_recipients() == ("one@example.test", "two@example.test")


def test_email_configuration_requires_a_nonblank_recipient() -> None:
    with pytest.raises(ValidationError):
        AppSettings(
            **BASE,
            alert_smtp_host="smtp.example.test",
            alert_email_from="alerts@example.test",
            alert_email_to=", ,",
        )
