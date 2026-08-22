"""Dashboard route regression coverage."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.ui import api_create_ui_router


def test_dashboard_labels_slo_as_scheduled_ingestion() -> None:
    """Distinguish an empty scheduled SLO window from missing ingestion history."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    assert response.status_code == 200
    assert "Scheduled ingestion success" in response.text
    assert "No scheduled runs" in response.text


def test_dashboard_formats_pnl_amounts_with_each_instruments_currency() -> None:
    """Render realized, unrealized, and total P&L as currency values."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    assert response.status_code == 200
    assert "currencyDisplay:'code'" in response.text
    assert "minimumFractionDigits:2" in response.text
    assert "maximumFractionDigits:2" in response.text
    assert "formatCurrency(item.realized_pnl,item.currency)" in response.text
    assert "formatCurrency(item.unrealized_pnl,item.currency)" in response.text
    assert "formatCurrency(item.total_pnl,item.currency)" in response.text


def test_dashboard_formats_positions_as_integers_or_three_decimal_numbers() -> None:
    """Render whole positions without decimals and round fractional positions."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    assert response.status_code == 200
    assert "minimumFractionDigits:0" in response.text
    assert "maximumFractionDigits:3" in response.text
    assert "formatPosition(item.position_qty)" in response.text


def test_dashboard_formats_business_dates_as_day_month_two_digit_year() -> None:
    """Render report dates without applying a timezone conversion."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    assert response.status_code == 200
    assert "function formatDate(value)" in response.text
    assert "`${match[3]}/${match[2]}/${match[1].slice(-2)}`" in response.text
    assert "formatDate(item.report_date_local)" in response.text


def test_dashboard_formats_timestamps_in_jerusalem_with_24_hour_time() -> None:
    """Render UTC instants as zero-padded Jerusalem dates and times."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    assert response.status_code == 200
    assert "new Intl.DateTimeFormat('en-GB'" in response.text
    assert "timeZone:'Asia/Jerusalem'" in response.text
    assert "day:'2-digit'" in response.text
    assert "month:'2-digit'" in response.text
    assert "year:'2-digit'" in response.text
    assert "hour:'2-digit'" in response.text
    assert "minute:'2-digit'" in response.text
    assert "hourCycle:'h23'" in response.text
    assert "formatDateTime(item.created_at_utc)" in response.text
    assert "formatDateTime(item.started_at_utc)" in response.text
