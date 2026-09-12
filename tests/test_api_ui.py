"""Portfolio and operations dashboard route regression coverage."""

import json
import re

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

    response = TestClient(application).get("/ui/transfers")

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
    assert "Transfer summary by currency" not in response.text
    assert "Total P&amp;L by instrument" in response.text
    assert (
        "<th>Symbol</th><th>Position</th><th>Average cost</th><th>Total cost</th>"
        "<th>Last-day value</th><th>Realized</th><th>Unrealized</th><th>Total</th>"
    ) in response.text
    assert "transfer-summary" not in response.text
    assert 'href="/ui/transfers"' in response.text
    assert 'id="transfers"' not in response.text
    assert 'href="/ui/costs"' in response.text
    assert 'href="/ui/operations"' in response.text


def test_main_page_places_supporting_summaries_after_instrument_pnl() -> None:
    """Keep the instrument P&L table ahead of its dividend and cash details."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    section_positions = [
        response.text.index("Total P&amp;L by instrument"),
        response.text.index("Net dividend payments"),
        response.text.index("Cash balances"),
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
        function makeNode(){return {children:[],_text:'',className:'',checked:true,value:'',
          get textContent(){return this._text+this.children.map(child=>child.textContent).join('')},
          set textContent(value){this._text=String(value);this.children=[]},
          append(...items){this.children.push(...items)},replaceChildren(){this.children=[]}}}
        const nodes={'pnl':makeNode(),'hide-zero-positions':makeNode(),'total-pnl':makeNode(),
          'symbol-search':makeNode(),'show-stocks':makeNode(),'show-options':makeNode(),'pnl-empty':makeNode()};
        const document={getElementById:id=>nodes[id],createElement:()=>makeNode()};
        const Intl={NumberFormat:function(){return {format:value=>String(value)}}};
        """
    )
    context.eval(script)
    context.eval(
        """
        latestPnlItems=[
          {symbol:'ZERO_FIXED',asset_category:'STK',position_qty:'0.00000000',currency:'USD',average_cost:null,total_cost:null,
           last_day_value:null,realized_pnl:'10',unrealized_pnl:'0',total_pnl:'10'},
          {symbol:'ZERO_EXPONENT',asset_category:'STK',position_qty:'0E-8',currency:'USD',average_cost:null,total_cost:null,
           last_day_value:null,realized_pnl:'20',unrealized_pnl:'0',total_pnl:'20'},
          {symbol:'OPEN',asset_category:'STK',position_qty:'2',currency:'USD',average_cost:null,total_cost:null,last_day_value:null,
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

    context.eval("""
        latestPnlItems=[
          {symbol:'AAPL',asset_category:'STK',position_qty:'2'},
          {symbol:'AAPL  260918C00200000',asset_category:'OPT',position_qty:'1'},
          {symbol:'MSFT',asset_category:'STK',position_qty:'3'},
          {symbol:'AAPL_CLOSED',asset_category:'STK',position_qty:'0'}];
        nodes['hide-zero-positions'].checked=true;
        nodes['symbol-search'].value=' aApL ';
        nodes['symbol-search'].oninput();
    """)
    assert context.eval("nodes.pnl.children.length") == 2
    context.eval("nodes['show-stocks'].checked=false;nodes['show-stocks'].onchange()")
    assert context.eval("nodes.pnl.children[0].children[0].textContent") == "AAPL  260918C00200000"
    context.eval("nodes['show-options'].checked=false;nodes['show-options'].onchange()")
    assert context.eval("nodes.pnl.children.length") == 0
    assert context.eval("nodes['pnl-empty'].textContent") == "No instruments match your filters."
    context.eval("nodes['show-stocks'].checked=true;nodes['show-stocks'].onchange()")
    assert context.eval("nodes.pnl.children[0].children[0].textContent") == "AAPL"
    context.eval("nodes['symbol-search'].value='';nodes['symbol-search'].oninput()")
    assert context.eval("nodes.pnl.children.length") == 2
    context.eval("nodes['hide-zero-positions'].checked=false;nodes['hide-zero-positions'].onchange()")
    assert context.eval("nodes.pnl.children.length") == 3
    assert context.eval("nodes['total-pnl'].textContent") == "unchanged"


def test_main_page_omits_intro_and_summary_date_captions() -> None:
    """Keep summary cards compact while retaining provisional-data warnings."""

    application = FastAPI()
    application.include_router(api_create_ui_router())

    response = TestClient(application).get("/ui")

    assert response.status_code == 200
    assert "Latest available IBKR data" not in response.text
    for identifier in ("pnl-report-date", "valuation-report-date", "cost-history-range"):
        assert identifier not in response.text
    assert 'id="pnl-state"' in response.text
    assert "Provisional totals" in response.text


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


def test_portfolio_shows_provisional_rows_and_totals() -> None:
    application = FastAPI()
    application.include_router(api_create_ui_router())
    script = TestClient(application).get("/ui").text.split("<script>", 1)[1].split("</script>", 1)[0]
    script = script.rsplit("loadAll();", 1)[0]
    context = quickjs.Context()
    context.eval("""
        function node(){return {children:[],_text:'',className:'',checked:true,value:'',
          get textContent(){return this._text+this.children.map(child=>child.textContent).join('')},
          set textContent(value){this._text=String(value);this.children=[]},
          append(...items){this.children.push(...items)},replaceChildren(){this.children=[]}}}
        const nodes={};const document={getElementById:id=>nodes[id]||(nodes[id]=node()),createElement:()=>node()};
        const Intl={NumberFormat:function(){return {format:value=>String(value)}}};
    """)
    context.eval(script)
    context.eval("""
        let row={symbol:'TEST',asset_category:'STK',position_qty:'1',currency:'USD',report_date_local:'2026-08-21',
          realized_pnl:'10',unrealized_pnl:'5',total_pnl:'15',provisional:true,unresolved_case_count:1};
        json=async()=>({items:[row]});loadPnl();
    """)
    while context.execute_pending_job():
        pass
    assert "Provisional" in context.eval("nodes.pnl.children[0].children[0].textContent")
    assert "Provisional" in context.eval("nodes['pnl-state'].textContent")
    assert context.eval("nodes['total-pnl'].className") == "metric bad"
    context.eval("row.provisional=false;row.unresolved_case_count=0;loadPnl()")
    while context.execute_pending_job():
        pass
    assert context.eval("nodes['pnl-state'].textContent") == ""
    assert context.eval("nodes['total-pnl'].className") == "metric"



def test_split_editor_preview_apply_cancel_and_unsupported_cases() -> None:
    """Execute correction controls; unsupported cases never offer completion."""
    application = FastAPI()
    application.include_router(api_create_ui_router())
    script = TestClient(application).get("/ui/operations").text.split("<script>", 1)[1].split("</script>", 1)[0]
    script = script.rsplit("loadAll();", 1)[0]
    context = quickjs.Context()
    context.eval("""
        function node(){return {children:[],textContent:'',value:'',className:'',checked:false,disabled:false,hidden:false,
          append(...items){this.children.push(...items)},replaceChildren(){this.children=[]}}}
        const nodes={};const document={getElementById:id=>nodes[id]||(nodes[id]=node()),createElement:()=>node()};
        const Intl={DateTimeFormat:function(){return {format:value=>'21/08/26 12:00'}},
          NumberFormat:function(locale,options){return {format:value=>Number(value).toFixed(options.maximumFractionDigits).replace(/0+$/, '').replace(/[.]$/, '')}}};
        let requests=[],fail=false;
        const items=[
          {case_id:'case-1',symbol:'TEST',action_type:'FORWARDSPLIT',status:'open',owner:null,
           created_at_utc:'2026-08-21T09:00:00Z',report_date_local:'2026-08-21',description:'Broker split',
           review_reason:'Missing ratio',required_check:'Check the broker ratio',requires_manual:true,can_correct_split:true},
          {case_id:'case-2',symbol:'OTHER',action_type:'SPINOFF',status:'resolved',owner:null,
           review_reason:'Unsupported spinoff',required_check:'Accounting support required',
           requires_manual:true,can_correct_split:false,resolution_note:'Previously reviewed'}];
    """)
    context.eval(script)
    context.eval("""
        const preview={preview_token:'token',factor:'1.5',snapshots:[{report_date_local:'2026-08-21',currency:'USD',
          before:{position_qty:'2',cost_basis:'201',realized_pnl:'0',unrealized_pnl:'19',total_pnl:'19',provisional:true},
          after:{position_qty:'3',cost_basis:'201',realized_pnl:'0',unrealized_pnl:'129',total_pnl:'129',provisional:false}}],
          lots_before:[{remaining_quantity:'2',open_price:'100',unit_basis:'100.5',cost_basis_open:'201'}],
          lots_after:[{remaining_quantity:'3',open_price:'66.66666667',unit_basis:'67',cost_basis_open:'201'}]};
        json=async(url,options)=>{
          if(!options)return {items};
          requests.push({url,body:JSON.parse(options.body)});if(fail)throw new Error('Preview is stale');
          if(url.endsWith('/apply')){items[0].requires_manual=false;items[0].can_correct_split=false;items[0].status='resolved'}
          return preview};
        loadAll=async()=>loadCases();loadCases();
    """)
    while context.execute_pending_job():
        pass
    assert context.eval("nodes['case-count'].textContent") == 1
    assert context.eval("nodes['unsupported-count'].textContent") == 1
    assert context.eval("nodes['unsupported-cases'].children[0].children.at(-1).children.length") == 0
    assert "Accounting support required" in context.eval("nodes['unsupported-cases'].children[0].children.map(x=>x.textContent).join(' ')")
    context.eval("nodes.cases.children[0].children.at(-1).children[0].onclick()")
    assert context.eval("nodes['split-editor'].hidden") is False
    assert context.eval("nodes['apply-split'].disabled") is True
    context.eval("nodes['new-shares'].value='3';nodes['old-shares'].value='2';nodes['split-note'].value='Broker notice';nodes['preview-split'].onclick()")
    while context.execute_pending_job():
        pass
    assert context.eval("requests.at(-1).url") == "/corporate-actions/cases/case-1/split/preview"
    assert context.eval("nodes['apply-split'].disabled") is False
    assert "129" in context.eval("nodes['split-snapshots'].children.map(r=>r.children.map(c=>c.textContent).join(' ')).join(' ')")
    assert context.eval("nodes['split-lots'].children[0].children[3].textContent") == "100.5"
    assert context.eval("nodes['split-lots'].children[1].children[3].textContent") == "67"
    context.eval("nodes['split-snapshots'].replaceChildren();preview.snapshots[0].after.position_qty='0.00000001';renderSplitPreview(preview)")
    assert context.eval("nodes['split-snapshots'].children[1].children[2].textContent") == "0.00000001"
    for quantity, expected in (("1E-8", "0.00000001"), ("1234567890123456.00000001", "1234567890123456.00000001"), ("2.50000000", "2.5"), ("-1E-8", "-0.00000001")):
        context.eval("nodes['split-snapshots'].replaceChildren();preview.snapshots[0].after.position_qty=" + json.dumps(quantity) + ";renderSplitPreview(preview)")
        assert context.eval("nodes['split-snapshots'].children[1].children[2].textContent") == expected
    context.eval("nodes['new-shares'].value='4';nodes['new-shares'].oninput()")
    assert context.eval("nodes['apply-split'].disabled") is True
    context.eval("nodes['apply-split'].onclick()")
    while context.execute_pending_job():
        pass
    assert context.eval("requests.length") == 1
    context.eval("nodes['cancel-split'].onclick()")
    assert context.eval("nodes['split-editor'].hidden") is True
    context.eval("nodes.cases.children[0].children.at(-1).children[0].onclick();nodes['new-shares'].value='3';nodes['old-shares'].value='2';nodes['split-note'].value='Broker notice';nodes['preview-split'].onclick()")
    while context.execute_pending_job():
        pass
    context.eval("fail=true;nodes['apply-split'].onclick()")
    while context.execute_pending_job():
        pass
    assert context.eval("nodes['case-error'].textContent") == "Preview is stale"
    assert context.eval("nodes['apply-split'].disabled") is True
    context.eval("fail=false;nodes['preview-split'].onclick()")
    while context.execute_pending_job():
        pass
    context.eval("nodes['apply-split'].onclick()")
    while context.execute_pending_job():
        pass
    assert context.eval("requests.at(-1).body.preview_token") == "token"
    assert context.eval("requests.at(-1).body.new_shares") == "3"
    assert context.eval("nodes['case-count'].textContent") == 0
    assert context.eval("nodes['empty-cases'].hidden") is False
    assert context.eval("nodes['split-editor'].hidden") is True


def _resolution_ui_context() -> quickjs.Context:
    """Run the shipped operations UI with only browser/network boundaries replaced."""
    application = FastAPI()
    application.include_router(api_create_ui_router())
    html = TestClient(application).get("/ui/operations").text
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    context = quickjs.Context()
    context.eval('const elementIds=' + json.dumps(re.findall(r'id="([^"]+)"', html)))
    context.eval("""
        function node(){return {children:[],_text:'',value:'',checked:false,disabled:false,hidden:false,
          get textContent(){return this._text+this.children.map(child=>child.textContent).join(' ')},
          set textContent(value){this._text=String(value);this.children=[]},
          append(...items){this.children.push(...items)},replaceChildren(){this._text='';this.children=[]}}}
        const nodes=Object.fromEntries(elementIds.map(id=>[id,node()]));
        const document={getElementById:id=>nodes[id]||null,createElement:()=>node()};
        const Intl={DateTimeFormat:function(){return {format:value=>'21/08/26 12:00'}},
          NumberFormat:function(locale,options){return {format:value=>options.currency+' '+value}}};
        const console={error:()=>{}};
        let requests=[],failure=null,refreshFailure=false,hold=false,release;
        const common={status:'open',owner:null,created_at_utc:'2026-08-21T09:00:00Z',
          report_date_local:'2026-08-21',requires_manual:true,can_correct_split:false};
        let items=[
          {...common,case_id:'transfer',symbol:'NEW',action_type:'IC',review_state:'actionable',
           description:'Identifier change',review_reason:'Paired broker legs',required_check:'Confirm unchanged ownership',
           resolution_options:[{type:'security_transfer',label:'Transfer existing position'}],broker_legs:[
             {symbol:'OLD',conid:'100',quantity:'-10',currency:'USD',cost_basis:null,report_date_local:'2026-08-21',description:'Debit old identifier'},
             {symbol:'NEW',conid:'200',quantity:'10',currency:'USD',cost_basis:'120',report_date_local:'2026-08-21',description:'Credit new identifier'}]},
          {...common,case_id:'distribution',symbol:'SPIN',action_type:'SO',review_state:'actionable',
           description:'Security distribution',review_reason:'Missing received security basis',required_check:'Confirm total basis from the broker',
           resolution_options:[{type:'distribution',label:'Record received security'}],broker_legs:[
             {symbol:'SPIN',conid:'300',quantity:'2',currency:'EUR',cost_basis:null,report_date_local:'2026-08-21',description:'Credited security'}]},
          {...common,case_id:'unsupported',symbol:'MERGED',action_type:'MERGER',review_state:'unsupported',
           review_reason:'Complex cash election',required_check:'Accounting support required',resolution_options:[],broker_legs:[]},
          {...common,case_id:'handled',symbol:'DONE',action_type:'IC',review_state:'handled',requires_manual:false,
           resolution_note:'Confirmed by statement',review_reason:'Already handled',required_check:'',resolution_options:[],broker_legs:[]}];
        const preview={case_id:'transfer',treatment:'security_transfer',summary:'Carry 10 units and USD 120 basis from OLD to NEW.',
          event:{event_corp_action_id:'event-transfer',action_id:'MOVE',report_date_local:'2026-08-21',
            source_symbol:'OLD',destination_symbol:'NEW',quantity:'10',currency:'USD',cost_basis:null,
            note:'Broker notice confirms unchanged ownership'},
          preview_token:'verified-inputs',applied:false,snapshots:[{symbol:'NEW',report_date_local:'2026-08-21',currency:'USD',
            before:{position_qty:'0',cost_basis:'0',realized_pnl:'0',unrealized_pnl:null,total_pnl:null,provisional:true},
            after:{position_qty:'10',cost_basis:'120',realized_pnl:'0',unrealized_pnl:'30',total_pnl:'30',provisional:false}}],
          lots_before:[{symbol:'OLD',remaining_quantity:'10',cost_basis_remaining:'120',currency:'USD',opened_at_utc:'2025-01-02T10:00:00Z'}],
          lots_after:[{symbol:'NEW',remaining_quantity:'10',cost_basis_remaining:'120',currency:'USD',opened_at_utc:'2025-01-02T10:00:00Z'}]};
        fetch=async(url,options)=>{
          if(!options){if(refreshFailure)throw new Error('Queue refresh failed');return {ok:true,status:200,json:async()=>({items})}}
          requests.push({url,body:JSON.parse(options.body)});
          if(hold)await new Promise(resolve=>{release=resolve});
          if(failure)return {ok:false,status:409,json:async()=>({message:failure})};
          if(url.endsWith('/apply')){const item=items.find(item=>url.includes('/'+item.case_id+'/'));
            item.review_state='handled';item.requires_manual=false;item.resolution_options=[]}
          return {ok:true,status:200,json:async()=>({...preview,applied:url.endsWith('/apply')})};
        };
    """)
    context.eval(script.rsplit("loadAll();", 1)[0])
    context.eval("loadSlo=async()=>{};loadPnl=async()=>{};loadLabels=async()=>{};loadRuns=async()=>{};loadCases()")
    _drain_ui_jobs(context)
    return context


def _drain_ui_jobs(context: quickjs.Context) -> None:
    while context.execute_pending_job():
        pass


def test_review_queue_separates_unsupported_and_preserves_handled_toggle() -> None:
    context = _resolution_ui_context()
    assert context.eval("nodes['case-count'].textContent") == '2'
    assert context.eval("nodes['unsupported-count'].textContent") == '1'
    assert context.eval("nodes.cases.children.length") == 2
    assert context.eval("nodes['unsupported-cases'].children.length") == 1
    assert context.eval("nodes['unsupported-cases'].children[0].children.at(-1).children.length") == 0
    assert context.eval("nodes['empty-cases'].hidden") is True
    context.eval("nodes['show-reviewed'].checked=true;nodes['show-reviewed'].onchange()")
    _drain_ui_jobs(context)
    assert context.eval("nodes.cases.children.length") == 3
    assert 'Confirmed by statement' in context.eval("nodes.cases.children[2].textContent")
    assert context.eval("nodes.cases.children[2].children.at(-1).children.length") == 0
    context.eval("items=items.filter(item=>item.review_state!=='actionable');loadCases()")
    _drain_ui_jobs(context)
    assert context.eval("nodes['case-count'].textContent") == '0'
    assert context.eval("nodes['empty-cases'].hidden") is False
    assert context.eval("nodes['unsupported-count'].textContent") == '1'


def test_transfer_preview_uses_broker_legs_and_applies_once() -> None:
    context = _resolution_ui_context()
    assert context.eval("nodes.cases.children[0].children.at(-1).children[0].textContent") == 'Preview transfer'
    context.eval("nodes.cases.children[0].children.at(-1).children[0].onclick()")
    assert context.eval("nodes['resolution-editor'].hidden") is False
    assert context.eval("nodes['distribution-basis-fields'].hidden") is True
    assert 'Confirm unchanged ownership' in context.eval("nodes['resolution-description'].textContent")
    broker_text = context.eval("nodes['resolution-legs'].textContent")
    for value in ('OLD', 'NEW', '100', '200', '-10', 'USD', 'Debit old identifier', 'N/A'):
        assert value in broker_text
    context.eval("nodes['preview-resolution'].onclick()")
    _drain_ui_jobs(context)
    assert context.eval("requests.length") == 0
    assert 'evidence' in context.eval("nodes['resolution-error'].textContent")
    context.eval("nodes['resolution-note'].value='Broker notice confirms unchanged ownership';hold=true;nodes['preview-resolution'].onclick();nodes['preview-resolution'].onclick()")
    _drain_ui_jobs(context)
    assert context.eval("requests.length") == 1
    assert context.eval("nodes['preview-resolution'].disabled") is True
    assert context.eval("nodes['cancel-resolution'].disabled") is True
    context.eval("release();hold=false")
    _drain_ui_jobs(context)
    assert context.eval("requests[0].url") == '/corporate-actions/cases/transfer/resolution/preview'
    assert json.loads(context.eval("JSON.stringify(requests[0].body)")) == {
        'treatment': 'security_transfer', 'note': 'Broker notice confirms unchanged ownership',
    }
    assert context.eval("nodes['apply-resolution'].disabled") is False
    assert context.eval("nodes['resolution-event-preview'].hidden") is False
    assert context.eval("nodes['resolution-event'].children.length") == 1
    event_text = context.eval("nodes['resolution-event'].textContent")
    for value in ('MOVE', '21/08/26', 'OLD', 'NEW', '10', 'Carry existing FIFO basis', 'Broker notice confirms unchanged ownership'):
        assert value in event_text
    context.eval("hold=true;nodes['apply-resolution'].onclick();nodes['apply-resolution'].onclick()")
    _drain_ui_jobs(context)
    assert context.eval("requests.length") == 2
    assert context.eval("requests[1].body.preview_token") == 'verified-inputs'
    context.eval("release();hold=false")
    _drain_ui_jobs(context)
    assert context.eval("nodes['resolution-editor'].hidden") is True
    assert context.eval("nodes['case-count'].textContent") == '1'


def test_distribution_requires_explicit_basis_and_invalidates_changed_preview() -> None:
    context = _resolution_ui_context()
    assert context.eval("nodes.cases.children[1].children.at(-1).children[0].textContent") == 'Enter distribution basis'
    context.eval("nodes.cases.children[1].children.at(-1).children[0].onclick()")
    assert context.eval("nodes['distribution-basis-fields'].hidden") is False
    assert 'EUR' in context.eval("nodes['distribution-basis-label'].textContent")
    assert '2' in context.eval("nodes['distribution-basis-label'].textContent")
    assert context.eval("nodes['distribution-basis'].value") == ''
    context.eval("nodes['resolution-note'].value='Verified total basis';nodes['preview-resolution'].onclick()")
    _drain_ui_jobs(context)
    assert context.eval("requests.length") == 0
    assert 'total cost basis' in context.eval("nodes['resolution-error'].textContent")
    for basis in ('-1', 'Infinity', 'abc'):
        context.eval("nodes['distribution-basis'].value=" + json.dumps(basis) + ";nodes['preview-resolution'].onclick()")
        _drain_ui_jobs(context)
        assert context.eval("requests.length") == 0
    context.eval("nodes['distribution-basis'].value='0';nodes['preview-resolution'].onclick()")
    _drain_ui_jobs(context)
    assert context.eval("requests.at(-1).body.cost_basis") == '0'
    assert context.eval("requests.at(-1).body.treatment") == 'distribution'
    assert context.eval("nodes['apply-resolution'].disabled") is False
    context.eval("nodes['distribution-basis'].value='25.50';nodes['distribution-basis'].oninput();nodes['apply-resolution'].onclick()")
    _drain_ui_jobs(context)
    assert context.eval("requests.length") == 1
    assert context.eval("nodes['apply-resolution'].disabled") is True
    assert context.eval("nodes['resolution-event'].children.length") == 0
    context.eval("hold=true;nodes['preview-resolution'].onclick();nodes['resolution-note'].oninput();release();hold=false")
    _drain_ui_jobs(context)
    assert context.eval("nodes['apply-resolution'].disabled") is True
    context.eval("nodes['preview-resolution'].onclick()")
    _drain_ui_jobs(context)
    context.eval("failure='Preview is stale';nodes['apply-resolution'].onclick()")
    _drain_ui_jobs(context)
    assert context.eval("nodes['resolution-error'].textContent") == 'Preview is stale'
    assert context.eval("nodes['apply-resolution'].disabled") is True
    assert context.eval("nodes['preview-resolution'].disabled") is False
    context.eval("failure=null;nodes['preview-resolution'].onclick()")
    _drain_ui_jobs(context)
    context.eval("refreshFailure=true;nodes['apply-resolution'].onclick()")
    _drain_ui_jobs(context)
    assert context.eval("requests.at(-1).body.cost_basis") == '25.50'
    assert context.eval("nodes['resolution-editor'].hidden") is True
    assert 'Queue refresh failed' in context.eval("nodes['case-error'].textContent")
    context.eval("refreshFailure=false;loadAll()")
    _drain_ui_jobs(context)
    assert context.eval("nodes['case-count'].textContent") == '1'
    assert context.eval("nodes['case-error'].textContent") == ''
    context.eval("nodes.cases.children[0].children.at(-1).children[0].onclick();nodes['cancel-resolution'].onclick()")
    assert context.eval("nodes['resolution-editor'].hidden") is True


def test_distribution_preview_uses_event_currency_and_explicit_zero_basis() -> None:
    context = _resolution_ui_context()
    context.eval("""
        preview.treatment='distribution';
        Object.assign(preview.event,{action_id:'DISTRIBUTION',source_symbol:null,destination_symbol:'SPIN',
          quantity:'2',currency:'EUR',cost_basis:'0',note:'Broker confirms zero basis'});
        nodes.cases.children[1].children.at(-1).children[0].onclick();
        nodes['distribution-basis'].value='0';nodes['resolution-note'].value='Broker confirms zero basis';
        nodes['preview-resolution'].onclick();
    """)
    _drain_ui_jobs(context)
    assert context.eval("nodes['resolution-event'].children.length") == 1
    assert context.eval("nodes['resolution-event'].children[0].children[5].textContent") == 'EUR 0'


def test_distribution_guidance_tracks_preview_and_input_changes() -> None:
    context = _resolution_ui_context()
    context.eval("nodes.cases.children[1].children.at(-1).children[0].onclick()")
    assert context.eval("nodes['apply-resolution'].disabled") is True
    guidance = context.eval("nodes['resolution-summary'].textContent")
    assert 'Preview changes' in guidance
    assert 'enable Apply treatment' in guidance

    context.eval("nodes['distribution-basis'].value='0';nodes['resolution-note'].value='Broker confirms zero basis'")
    for field in ('distribution-basis', 'resolution-note'):
        context.eval("hold=true;nodes['preview-resolution'].onclick()")
        _drain_ui_jobs(context)
        assert 'Preparing preview' in context.eval("nodes['resolution-summary'].textContent")
        assert context.eval("nodes['apply-resolution'].disabled") is True
        context.eval("release();hold=false")
        _drain_ui_jobs(context)
        assert context.eval("nodes['apply-resolution'].disabled") is False
        assert 'click Apply treatment to save' in context.eval("nodes['resolution-summary'].textContent")

        context.eval("nodes[" + json.dumps(field) + "].oninput()")
        assert context.eval("nodes['apply-resolution'].disabled") is True
        assert 'enable Apply treatment' in context.eval("nodes['resolution-summary'].textContent")

    context.eval("failure='Preview is stale';nodes['preview-resolution'].onclick()")
    _drain_ui_jobs(context)
    assert context.eval("nodes['resolution-error'].textContent") == 'Preview is stale'
    assert 'enable Apply treatment' in context.eval("nodes['resolution-summary'].textContent")

    context.eval("failure=null;nodes['preview-resolution'].onclick()")
    _drain_ui_jobs(context)
    context.eval("hold=true;nodes['apply-resolution'].onclick()")
    _drain_ui_jobs(context)
    assert 'Applying treatment' in context.eval("nodes['resolution-summary'].textContent")
    context.eval("release();hold=false")
    _drain_ui_jobs(context)
    assert context.eval("requests.at(-1).body.cost_basis") == '0'
    assert context.eval("nodes['resolution-editor'].hidden") is True


def test_distribution_preview_shows_one_event_despite_daily_snapshot_history() -> None:
    context = _resolution_ui_context()
    context.eval("""
        items[1].symbol='DVLT.CNT';items[1].report_date_local='2026-02-23';
        Object.assign(items[1].broker_legs[0],{symbol:'DVLT.CNT',quantity:'5',currency:'USD',report_date_local:'2026-02-23'});
        preview.treatment='distribution';preview.summary='Record 5 credited units with total cost basis 25 USD.';
        Object.assign(preview.event,{action_id:'161624952',report_date_local:'2026-02-23',source_symbol:null,
          destination_symbol:'DVLT.CNT',quantity:'5',cost_basis:'25',note:'Verified broker basis'});
        preview.snapshots=['2026-09-10','2026-09-11','2026-09-09'].map(day=>({
          ...preview.snapshots[0],symbol:'DVLT.CNT',report_date_local:day}));
        nodes.cases.children[1].children.at(-1).children[0].onclick();
        nodes['distribution-basis'].value='25';nodes['resolution-note'].value='Verified broker basis';
        nodes['preview-resolution'].onclick();
    """)
    _drain_ui_jobs(context)
    assert context.eval("Object.hasOwn(nodes,'resolution-event')") is True
    assert context.eval("nodes['resolution-event'].children.length") == 1
    assert json.loads(context.eval("JSON.stringify(nodes['resolution-event'].children[0].children.map(cell=>cell.textContent))")) == [
        '161624952', '23/02/26', 'Distribution', 'DVLT.CNT', '5', 'USD 25', 'Verified broker basis',
    ]
    for removed_id in ('resolution-snapshots', 'resolution-history', 'resolution-history-snapshots', 'resolution-lots'):
        assert context.eval('elementIds.includes(' + json.dumps(removed_id) + ')') is False
    context.eval("nodes['preview-resolution'].onclick()")
    _drain_ui_jobs(context)
    assert context.eval("nodes['resolution-event'].children.length") == 1
    context.eval("nodes['distribution-basis'].oninput()")
    assert context.eval("nodes['resolution-event'].children.length") == 0
    assert context.eval("nodes['resolution-event-preview'].hidden") is True
    assert context.eval("nodes['apply-resolution'].disabled") is True


def test_resolution_preview_renders_current_server_event_instead_of_stale_case() -> None:
    context = _resolution_ui_context()
    context.eval("""
        Object.assign(preview.event,{report_date_local:'2026-08-22',source_symbol:'UPDATED.OLD',
          destination_symbol:'UPDATED.NEW',quantity:'12',note:'Verified updated event'});
        nodes.cases.children[0].children.at(-1).children[0].onclick();
        nodes['resolution-note'].value='Verified updated event';nodes['preview-resolution'].onclick();
    """)
    _drain_ui_jobs(context)
    assert context.eval("nodes['resolution-event'].children.length") == 1
    assert context.eval("nodes['resolution-event'].children[0].children[1].textContent") == '22/08/26'
    assert context.eval("nodes['resolution-event'].children[0].children[3].textContent") == 'UPDATED.OLD → UPDATED.NEW'
    assert context.eval("nodes['resolution-event'].children[0].children[4].textContent") == '12'
