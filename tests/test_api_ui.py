"""Portfolio and operations dashboard route regression coverage."""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import quickjs

from app.api.routers.ui import api_create_ui_router


def test_dashboard_labels_slo_as_scheduled_ingestion() -> None:
    """Distinguish an empty scheduled SLO window from missing ingestion history."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui/operations")

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

    response = TestClient(application).get("/ui/operations")

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


def test_main_page_exposes_portfolio_summary_and_requested_tables() -> None:
    """Keep the portfolio overview at /ui with the approved report sections."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    assert response.status_code == 200
    assert "Latest portfolio P&amp;L" in response.text
    assert "Estimated net liquidation value" in response.text
    assert "Net transfers (USD)" in response.text
    assert "Total profit (USD)" in response.text
    assert "Cash balances" in response.text
    assert "Transfer summary by currency" in response.text
    assert "Total P&amp;L by instrument" in response.text
    assert "Transfer history" in response.text
    assert (
        "<th>Symbol</th><th>Position</th><th>Average cost</th><th>Total cost</th>"
        "<th>Last-day value</th><th>Realized</th><th>Unrealized</th><th>Total</th>"
    ) in response.text
    assert "<th>Currency</th><th>Net transfers</th><th>Gross deposits</th><th>Gross withdrawals</th>" in response.text
    assert "<th>Date</th><th>Type</th><th>Amount</th><th>Currency</th><th>Description</th>" in response.text
    assert 'href="/ui/operations"' in response.text


def test_main_page_renders_derived_instrument_cost_and_value_fields() -> None:
    """Display the API's per-unit cost, total cost, and end-of-day position value."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    assert response.status_code == 200
    assert "formatCurrency(item.average_cost,item.currency)" in response.text
    assert "formatCurrency(item.total_cost,item.currency)" in response.text
    assert "formatCurrency(item.last_day_value,item.currency)" in response.text


def test_main_page_displays_na_instead_of_zero_for_null_values() -> None:
    """Do not let JavaScript's numeric null coercion fabricate a USD zero."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    assert response.status_code == 200
    assert "if(value===null||value===undefined)return 'N/A'" in response.text
    assert "function formatPercent(value){if(value===null||value===undefined)return 'N/A'" in response.text
    assert "Unavailable" not in response.text


def test_main_page_hides_zero_positions_by_default_without_filtering_totals() -> None:
    """Keep closed instruments out of the table while retaining their realized P&L in totals."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    assert response.status_code == 200
    assert '<input id="hide-zero-positions" type="checkbox" checked>' in response.text
    assert "const hideZero=el('hide-zero-positions').checked" in response.text
    assert "if(hideZero&&Number(item.position_qty)===0)continue" in response.text
    assert "for(const item of latestPnlItems){realized+=Number(item.realized_pnl)" in response.text
    assert "el('hide-zero-positions').onchange=renderPnl" in response.text


def test_main_page_toggle_executes_zero_position_filter_without_changing_totals() -> None:
    """Execute the shipped JavaScript against numeric zero variants and a null-valued open row."""

    application = FastAPI()
    application.include_router(api_create_ui_router())
    response = TestClient(application).get("/ui")
    script = response.text.split("<script>", 1)[1].split("</script>", 1)[0].rsplit("loadAll();", 1)[0]
    context = quickjs.Context()
    context.eval(
        """
        function makeNode(){return {children:[],textContent:'',className:'',checked:true,
          append(...items){this.children.push(...items)},replaceChildren(){this.children=[]}}}
        const nodes={'pnl':makeNode(),'hide-zero-positions':makeNode(),'total-pnl':makeNode()};
        const document={getElementById:id=>nodes[id],createElement:()=>makeNode()};
        const Intl={NumberFormat:function(){return {format:value=>String(value)}}};
        """
    )
    context.eval(script)
    context.eval(
        """
        latestPnlItems=[
          {symbol:'ZERO_FIXED',position_qty:'0.00000000',currency:'USD',average_cost:null,total_cost:null,
           last_day_value:null,realized_pnl:'10',unrealized_pnl:'0',total_pnl:'10'},
          {symbol:'ZERO_EXPONENT',position_qty:'0E-8',currency:'USD',average_cost:null,total_cost:null,
           last_day_value:null,realized_pnl:'20',unrealized_pnl:'0',total_pnl:'20'},
          {symbol:'OPEN',position_qty:'2',currency:'USD',average_cost:null,total_cost:null,last_day_value:null,
           realized_pnl:'1',unrealized_pnl:'2',total_pnl:'3'}];
        nodes['total-pnl'].textContent='unchanged';
        renderPnl();
        """
    )

    hidden_rows = json.loads(context.eval("JSON.stringify(nodes.pnl.children.map(row=>row.children.map(cell=>cell.textContent)))"))
    assert hidden_rows == [["OPEN", "2", "N/A", "N/A", "N/A", "1", "2", "3"]]
    assert context.eval("nodes['total-pnl'].textContent") == "unchanged"

    context.eval("nodes['hide-zero-positions'].checked=false;nodes['hide-zero-positions'].onchange()")
    visible_symbols = json.loads(
        context.eval("JSON.stringify(nodes.pnl.children.map(row=>row.children[0].textContent))")
    )
    assert visible_symbols == ["ZERO_FIXED", "ZERO_EXPONENT", "OPEN"]
    assert context.eval("nodes['total-pnl'].textContent") == "unchanged"


def test_main_page_labels_pnl_and_valuation_dates_independently() -> None:
    """Do not let the last completed request overwrite a different report date."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    assert response.status_code == 200
    assert 'id="pnl-report-date"' in response.text
    assert 'id="valuation-report-date"' in response.text
    assert "el('pnl-report-date').textContent=latest===null?'N/A':'As of '+formatDate(latest)" in response.text
    assert "el('valuation-report-date').textContent=x.report_date_local?'As of '+formatDate(x.report_date_local):'N/A'" in response.text
    assert 'id="report-date"' not in response.text


def test_operations_page_preserves_existing_dashboard_and_links_to_portfolio() -> None:
    """Keep the original operations dashboard available as a separate page."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui/operations")

    assert response.status_code == 200
    assert "Scheduled ingestion success" in response.text
    assert "Corporate-action review queue" in response.text
    assert "Recent ingestion runs" in response.text
    assert 'href="/ui"' in response.text
