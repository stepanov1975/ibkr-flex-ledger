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
    assert "Total costs (USD)" in response.text
    assert "Net dividend payments" in response.text
    assert "Cash balances" in response.text
    assert "Cost summary by category" not in response.text
    assert "Transfer summary by currency" in response.text
    assert "Total P&amp;L by instrument" in response.text
    assert "Transfer history" in response.text
    assert (
        "<th>Symbol</th><th>Position</th><th>Average cost</th><th>Total cost</th>"
        "<th>Last-day value</th><th>Realized</th><th>Unrealized</th><th>Total</th>"
    ) in response.text
    assert "<th>Currency</th><th>Net transfers</th><th>Gross deposits</th><th>Gross withdrawals</th>" in response.text
    assert "<th>Date</th><th>Type</th><th>Amount</th><th>Currency</th><th>Description</th>" in response.text
    assert 'href="/ui/costs"' in response.text
    assert 'href="/ui/operations"' in response.text


def test_main_page_places_supporting_summaries_after_instrument_pnl() -> None:
    """Keep the instrument P&L table ahead of its dividend, cash, and transfer details."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    section_positions = [
        response.text.index("Total P&amp;L by instrument"),
        response.text.index("Net dividend payments"),
        response.text.index("Cash balances"),
        response.text.index("Transfer summary by currency"),
        response.text.index("Transfer history"),
    ]
    assert section_positions == sorted(section_positions)


def test_costs_page_renders_cost_treatment_breakdown() -> None:
    """Explain which cost categories affect the instrument P&L bridge on their own page."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui/costs")

    assert response.status_code == 200
    assert 'id="costs-outside-pnl-usd"' in response.text
    assert 'id="cost-history-range"' in response.text
    assert 'id="cost-summary"' in response.text
    assert "<th>Category</th><th>Net cost</th><th>P&amp;L treatment</th>" in response.text
    assert "item.included_in_instrument_pnl?'Included':'Outside'" in response.text


def test_costs_page_places_securities_commission_summary_above_categories() -> None:
    """Show the requested buy/sell commission table before the category summary."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui/costs")

    assert response.status_code == 200
    assert response.text.index("Securities commissions") < response.text.index("Cost summary by category")
    assert "<th>Instrument type</th><th>Side</th><th>Executions</th><th>Commission</th>" in response.text
    assert 'id="securities-commission-summary"' in response.text
    assert 'id="securities-commission-coverage"' in response.text
    assert 'id="securities-commission-total"' in response.text
    assert "Total buys" in response.text
    assert "Total sells" in response.text
    assert "Grand total" in response.text
    assert 'href="/ui"' in response.text


def test_costs_page_executes_commission_totals_and_unavailable_values() -> None:
    """Execute the shipped Costs-page JavaScript for complete and missing commission values."""

    application = FastAPI()
    application.include_router(api_create_ui_router())
    response = TestClient(application).get("/ui/costs")
    script = response.text.split("<script>", 1)[1].split("</script>", 1)[0].rsplit("loadCosts()", 1)[0]
    context = quickjs.Context()
    context.eval(
        """
        function makeNode(){return {children:[],textContent:'',className:'',
          append(...items){this.children.push(...items)},replaceChildren(){this.children=[]}}}
        const nodeIds=['securities-commission-summary','buy-execution-total','buy-commission-total',
          'sell-execution-total','sell-commission-total','securities-execution-total',
          'securities-commission-total','securities-commission-coverage','cost-summary',
          'costs-outside-pnl-usd','cost-history-range'];
        const nodes=Object.fromEntries(nodeIds.map(id=>[id,makeNode()]));
        const document={getElementById:id=>nodes[id],createElement:()=>makeNode()};
        const Intl={NumberFormat:function(){return {format:value=>'USD '+Number(value).toFixed(2)}}};
        """
    )
    context.eval(script)
    context.eval(
        """
        let responsePayload={
          securities_commission_summary:[
            {instrument_type:'Stocks',side:'BUY',execution_count:2,commission_usd:'7'},
            {instrument_type:'Options',side:'SELL',execution_count:1,commission_usd:'5'}],
          securities_commission_execution_count:3,securities_commission_instrument_count:3,
          securities_commission_total_usd:'12',securities_commission_date_from:'2026-08-01',
          securities_commission_date_to:'2026-08-20',cost_summary:[],
          costs_outside_instrument_pnl_usd:'3',activity_date_from:'2026-08-01',activity_date_to:'2026-08-20'};
        json=async()=>responsePayload;loadCosts();
        """
    )
    while context.execute_pending_job():
        pass

    rendered_rows = json.loads(
        context.eval(
            "JSON.stringify(nodes['securities-commission-summary'].children.map("
            "row=>row.children.map(cell=>cell.textContent)))"
        )
    )
    assert rendered_rows == [
        ["Stocks", "Buy", "2", "USD 7.00"],
        ["Options", "Sell", "1", "USD 5.00"],
    ]
    assert context.eval("nodes['buy-commission-total'].textContent") == "USD 7.00"
    assert context.eval("nodes['sell-commission-total'].textContent") == "USD 5.00"
    assert context.eval("nodes['securities-commission-total'].textContent") == "USD 12.00"

    context.eval(
        """
        responsePayload.securities_commission_summary[1].commission_usd=null;
        responsePayload.securities_commission_total_usd=null;
        loadCosts();
        """
    )
    while context.execute_pending_job():
        pass

    assert context.eval("nodes['buy-commission-total'].textContent") == "USD 7.00"
    assert context.eval("nodes['sell-commission-total'].textContent") == "N/A"
    assert context.eval("nodes['securities-commission-total'].textContent") == "N/A"


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
