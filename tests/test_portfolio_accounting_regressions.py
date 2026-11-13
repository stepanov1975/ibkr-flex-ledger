"""Published-import regressions for rebates and incomplete report valuations."""

from decimal import Decimal
import xml.etree.ElementTree as ET

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.analytics.account_insights import _NAV_COMPONENTS
from app.api.routers.reports import api_create_reports_router
from app.config import AppSettings
from app.db import SQLAlchemyPortfolioService
from app.db.account_insights import db_account_insights
from test_end_to_end_seeded import _SEEDED_PAYLOAD
from test_ingestion_integrity_regressions import _harness, database as _database


database = _database


@pytest.mark.parametrize("commission", [Decimal("-1"), Decimal("0"), Decimal("1")])
@pytest.mark.parametrize("closed", [False, True])
def test_signed_commissions_agree_in_fifo_costs_and_reconciliation(database, commission, closed):
    orchestrator, adapter, *_ = _harness(database)
    report = ET.fromstring(_SEEDED_PAYLOAD)
    trade = report.find(".//Trade")
    trade.set("ibCommission", str(commission))
    trade.set("netCash", str(-200 + commission))
    position = report.find(".//OpenPosition")
    position.set("multiplier", "1")
    position.set("fifoPnlUnrealized", str(20 + commission))
    if closed:
        sale = {**trade.attrib, "transactionID": "9010", "ibExecID": "SELL-EXEC", "buySell": "SELL",
                "quantity": "-2", "tradePrice": "110", "dateTime": "20260821;140000",
                "netCash": str(220 + commission), "fifoPnlRealized": str(20 + 2 * commission)}
        ET.SubElement(report.find(".//Trades"), "Trade", sale)
        report.find(".//OpenPositions").remove(position)
    adapter.payload_bytes = ET.tostring(report)
    assert orchestrator.job_execute("ingestion_run").status == "success"

    repository = SQLAlchemyPortfolioService(database)
    snapshot, = repository.db_report_pnl_by_instrument("INTEGRITY", None, None, None)
    reconciliation, = repository.db_reconciliation_sources("INTEGRITY", None, None, None)
    costs = repository.db_report_portfolio_summary("INTEGRITY")
    fee_cost = -commission * (2 if closed else 1)
    assert not snapshot.provisional
    assert Decimal(snapshot.realized_pnl) == (20 + 2 * commission if closed else 0)
    assert Decimal(snapshot.unrealized_pnl) == (0 if closed else 20 + commission)
    if not closed:
        assert Decimal(snapshot.cost_basis) == 200 - commission
    assert Decimal(reconciliation.fees) == fee_cost
    assert Decimal(reconciliation.broker_fees) == fee_cost
    assert Decimal(costs.total_costs_usd) == fee_cost


def test_account_nav_requires_summary_for_every_security(database):
    orchestrator, adapter, *_ = _harness(database)
    components = " ".join(f'{key}="0"' for key in _NAV_COMPONENTS if key not in ("cash", "stock"))
    equity = (
        f'<EquitySummaryInBase><EquitySummaryByReportDateInBase reportDate="20260821" '
        f'currency="USD" total="300" cash="30" stock="270" {components}/></EquitySummaryInBase>'
    ).encode()
    lot = (b'<OpenPosition conid="900099" symbol="DETAIL_ONLY" assetCategory="STK" currency="USD" '
           b'levelOfDetail="LOT" reportDate="20260821" position="1" markPrice="50" '
           b'positionValue="50" fxRateToBase="1"/>')
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b"</OpenPositions>", lot + b"</OpenPositions>").replace(
        b"</FlexStatement>", equity + b"</FlexStatement>",
    )
    assert orchestrator.job_execute("ingestion_run").status == "success"
    nav = db_account_insights(database, "INTEGRITY")["nav"]
    assert nav["cash_and_positions"] is None
    assert nav["check"]["status"] == "not_comparable"
    assert "900099" in nav["check"]["reason"]


@pytest.mark.parametrize("missing_input", ["markPrice", "multiplier", "fxRateToBase", None])
def test_last_day_value_requires_market_inputs(database, missing_input):
    orchestrator, adapter, *_ = _harness(database)
    report = ET.fromstring(_SEEDED_PAYLOAD)
    position = report.find(".//OpenPosition")
    position.set("multiplier", "1")
    if missing_input is not None:
        position.attrib.pop(missing_input)
    if missing_input == "fxRateToBase":
        position.set("currency", "EUR")
    adapter.payload_bytes = ET.tostring(report)
    assert orchestrator.job_execute("ingestion_run").status == "success"
    app = FastAPI()
    settings = AppSettings(_env_file=None, account_id="INTEGRITY", ibkr_flex_token="test", ibkr_flex_query_id="test")
    app.include_router(api_create_reports_router(settings, SQLAlchemyPortfolioService(database)))
    with TestClient(app) as client:
        response = client.get("/reports/pnl/by-instrument")
    assert response.status_code == 200
    item, = response.json()["items"]
    assert Decimal(item["total_cost"]) == Decimal("201")
    assert item["provisional"] is (missing_input is not None)
    if missing_input is None:
        assert Decimal(item["last_day_value"]) == Decimal("220")
    else:
        assert item["last_day_value"] is None
