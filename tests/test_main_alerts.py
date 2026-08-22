"""Scheduled outbound SLO alert evaluator entrypoint tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from app import bootstrap as bootstrap_module
from app import main as main_module
from app.config import AppSettings
from app.operations import AlertEvaluationResult


NOW = datetime(2026, 8, 22, 12, 30, tzinfo=timezone.utc)


def _settings(**overrides: object) -> AppSettings:
    return AppSettings(
        environment_name="test",
        database_url="postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        account_id="U_TEST",
        ibkr_flex_token="token",
        ibkr_flex_query_id="query",
        **overrides,
    )


def test_alerts_evaluate_cli_prints_channel_names_without_sensitive_configuration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["stock-app", "alerts-evaluate"])
    monkeypatch.setattr(
        main_module,
        "bootstrap_evaluate_slo_alerts",
        lambda: AlertEvaluationResult(("webhook",), ()),
    )

    main_module.main()

    captured = capsys.readouterr()
    assert captured.out == "delivered_channels=webhook failed_channels=none\n"
    assert captured.err == ""


def test_alerts_evaluate_cli_exits_nonzero_when_delivery_failed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["stock-app", "alerts-evaluate"])
    monkeypatch.setattr(
        main_module,
        "bootstrap_evaluate_slo_alerts",
        lambda: AlertEvaluationResult((), ("email",)),
    )

    with pytest.raises(SystemExit) as raised:
        main_module.main()

    assert raised.value.code == 1
    assert capsys.readouterr().out == "delivered_channels=none failed_channels=email\n"


def test_alerts_evaluate_cli_reports_sanitized_no_channel_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_bootstrap() -> AlertEvaluationResult:
        raise bootstrap_module.AlertConfigurationError(
            "no outbound alert channel is configured"
        )

    monkeypatch.setattr(sys, "argv", ["stock-app", "alerts-evaluate"])
    monkeypatch.setattr(main_module, "bootstrap_evaluate_slo_alerts", fail_bootstrap)

    with pytest.raises(SystemExit) as raised:
        main_module.main()

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert captured.err == "no outbound alert channel is configured\n"
    assert "hooks.example.test/secret" not in captured.out + captured.err


def test_alerts_evaluate_cli_sanitizes_invalid_real_configuration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    malformed_url = "ftp://hook-user:hook-secret@hooks.example.test/private-path"
    recipient = "private-owner@example.test"
    smtp_password = "smtp-private-secret"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IBKR_FLEX_TOKEN", "token")
    monkeypatch.setenv("IBKR_FLEX_QUERY_ID", "query")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", malformed_url)
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ALERT_SMTP_USERNAME", "private-user")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", smtp_password)
    monkeypatch.setenv("ALERT_EMAIL_FROM", "alerts@example.test")
    monkeypatch.setenv("ALERT_EMAIL_TO", recipient)
    monkeypatch.setattr(sys, "argv", ["stock-app", "alerts-evaluate"])

    with pytest.raises(SystemExit) as raised:
        main_module.main()

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert captured.err == "outbound alert configuration is invalid\n"
    assert malformed_url not in captured.err
    assert recipient not in captured.err
    assert smtp_password not in captured.err


def test_alert_bootstrap_wires_one_engine_configured_channels_and_30_day_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        alert_webhook_url="https://hooks.example.test/secret",
        alert_delivery_timeout_seconds=7,
        alert_smtp_host="smtp.example.test",
        alert_smtp_port=2525,
        alert_smtp_starttls=False,
        alert_smtp_username="user",
        alert_smtp_password="password",
        alert_email_from="alerts@example.test",
        alert_email_to="one@example.test, two@example.test",
    )
    engine = object()
    rows = [object()]
    portfolio_repository = Mock()
    portfolio_repository.db_ingestion_slo_records.return_value = rows
    state_repository = object()
    webhook_sender = object()
    email_sender = object()
    status = object()
    expected_result = AlertEvaluationResult(("webhook", "email"), ())
    create_engine = Mock(return_value=engine)
    create_portfolio_repository = Mock(return_value=portfolio_repository)
    create_state_repository = Mock(return_value=state_repository)
    create_webhook_sender = Mock(return_value=webhook_sender)
    create_email_sender = Mock(return_value=email_sender)
    build_status = Mock(return_value=status)
    evaluate = Mock(return_value=expected_result)

    class _FixedDatetime:
        @staticmethod
        def now(tz: timezone) -> datetime:
            assert tz is timezone.utc
            return NOW

    monkeypatch.setattr(bootstrap_module, "config_load_settings", lambda: settings)
    monkeypatch.setattr(bootstrap_module, "db_create_engine", create_engine)
    monkeypatch.setattr(bootstrap_module, "datetime", _FixedDatetime)
    monkeypatch.setattr(
        bootstrap_module, "SQLAlchemyPortfolioService", create_portfolio_repository
    )
    monkeypatch.setattr(
        bootstrap_module, "SQLAlchemyOperationsAlertService", create_state_repository
    )
    monkeypatch.setattr(bootstrap_module, "WebhookAlertSender", create_webhook_sender)
    monkeypatch.setattr(bootstrap_module, "SmtpAlertSender", create_email_sender)
    monkeypatch.setattr(bootstrap_module, "operations_build_slo_status", build_status)
    monkeypatch.setattr(bootstrap_module, "operations_evaluate_slo_alerts", evaluate)

    result = bootstrap_module.bootstrap_evaluate_slo_alerts()

    assert result == expected_result
    create_engine.assert_called_once_with(database_url=settings.database_url)
    create_portfolio_repository.assert_called_once_with(engine=engine)
    create_state_repository.assert_called_once_with(engine=engine)
    create_webhook_sender.assert_called_once_with(
        "https://hooks.example.test/secret", timeout=7
    )
    create_email_sender.assert_called_once_with(
        host="smtp.example.test",
        port=2525,
        sender="alerts@example.test",
        recipients=("one@example.test", "two@example.test"),
        starttls=False,
        username="user",
        password="password",
        timeout=7,
    )
    portfolio_repository.db_ingestion_slo_records.assert_called_once_with(
        "U_TEST", NOW - timedelta(days=30)
    )
    build_status.assert_called_once_with(rows, measured_at_utc=NOW)
    evaluate.assert_called_once_with(
        "U_TEST", status, state_repository, [webhook_sender, email_sender], NOW
    )


def test_alert_bootstrap_rejects_runs_without_a_configured_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = object()
    create_webhook_sender = Mock()
    create_email_sender = Mock()
    monkeypatch.setattr(bootstrap_module, "config_load_settings", _settings)
    monkeypatch.setattr(
        bootstrap_module, "db_create_engine", Mock(return_value=engine)
    )
    monkeypatch.setattr(
        bootstrap_module, "SQLAlchemyPortfolioService", Mock(return_value=object())
    )
    monkeypatch.setattr(
        bootstrap_module,
        "SQLAlchemyOperationsAlertService",
        Mock(return_value=object()),
    )
    monkeypatch.setattr(bootstrap_module, "WebhookAlertSender", create_webhook_sender)
    monkeypatch.setattr(bootstrap_module, "SmtpAlertSender", create_email_sender)

    with pytest.raises(
        bootstrap_module.AlertConfigurationError,
        match="^no outbound alert channel is configured$",
    ):
        bootstrap_module.bootstrap_evaluate_slo_alerts()

    create_webhook_sender.assert_not_called()
    create_email_sender.assert_not_called()
