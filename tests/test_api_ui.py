"""Dashboard route regression coverage."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.ui import api_create_ui_router


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
