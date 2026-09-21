"""Execute account insights rendering, error recovery and safe broker text."""

import json

from fastapi.encoders import jsonable_encoder
from decimal import Decimal
import quickjs

from app.analytics.account_insights import AccountInsights
from app.api.routers.account_insights_ui import ACCOUNT_INSIGHTS_HTML
from test_account_insights import sample


def context(report, fail=False):
    ctx = quickjs.Context()
    ctx.eval("""
function element(tag){return {tagName:tag,children:[],textContent:'',disabled:false,
append(...items){this.children.push(...items)},replaceChildren(){this.children=[]},setAttribute(){}}}
const nodes={content:element('div'),status:element('p'),reload:element('button')};
const document={getElementById:id=>nodes[id],createElement:element,createElementNS:(ns,tag)=>element(tag)};
function text(n){return n.textContent+' '+n.children.map(text).join(' ')}
""")
    ctx.eval("let fail=" + json.dumps(fail) + ";const data=" + json.dumps(jsonable_encoder(report, custom_encoder={Decimal: str})) + ";")
    ctx.eval("async function fetch(){return {ok:!fail,json:async()=>data}}")
    ctx.eval(ACCOUNT_INSIGHTS_HTML.split("<script>")[1].split("</script>")[0])
    while ctx.execute_pending_job():
        pass
    return ctx


def test_account_ui_renders_values_and_does_not_interpret_broker_html():
    rows = sample()
    rows[0].payload["currency"] = "<script>alert(1)</script>"
    report = AccountInsights(rows, rows[:1]).build()
    ctx = context(report)
    assert "18/09/26" in ctx.eval("JSON.stringify(nodes.content)")
    assert "<script>alert(1)</script>" in ctx.eval("JSON.stringify(nodes.content)")
    assert ctx.eval("nodes.reload.disabled") is False


def test_account_ui_error_empty_and_retry():
    ctx = context(AccountInsights([], []).build(), True)
    assert "Unable" in ctx.eval("nodes.status.textContent")
    assert ctx.eval("nodes.content.children.length") == 0
    ctx.eval("fail=false;nodes.reload.onclick()")
    while ctx.execute_pending_job():
        pass
    assert "NAV section unavailable" in ctx.eval("JSON.stringify(nodes.content)")
    assert "Loaded" in ctx.eval("nodes.status.textContent")


def test_income_ui_keeps_pending_and_paid_separate():
    from app.analytics.account_income import account_income
    from test_account_income import accrual
    report = AccountInsights([], []).build()
    report['income'] = account_income([accrual()], [accrual()], [])
    ctx = context(report)
    rendered = ctx.eval('JSON.stringify(nodes.content)')
    assert 'Expected net' in rendered
    assert 'USD 8.00' in rendered
    assert 'not comparable' in rendered


def test_lending_ui_shows_owned_and_lent_separately():
    from app.analytics.account_holdings import account_lending
    from test_account_insights import row
    report = AccountInsights([], []).build()
    report['lending'] = account_lending([row('NetStockPositionSummary', conid='1', symbol='LENT',
        sharesAtIb='100', sharesLent='-40', sharesBorrowed='0', netShares='60')])
    rendered = context(report).eval('JSON.stringify(nodes.content)')
    assert 'LENT' in rendered
    assert '40.00%' in rendered
    assert 'Owned shares' in rendered


def test_settled_cash_ui_explains_negative_balances():
    from app.analytics.account_holdings import account_settled_cash
    from test_account_insights import row
    report = AccountInsights([], []).build()
    report['settled_cash'] = account_settled_cash([row('CashReport', currency='USD',
        endingCash='-10', endingSettledCash='-15')])
    rendered = context(report).eval('JSON.stringify(nodes.content)')
    assert 'USD -15.00' in rendered
    assert 'not buying power' in rendered


def test_option_ui_links_to_existing_instrument_history():
    from app.analytics.account_options import account_option_activity
    from test_account_options import option
    report = AccountInsights([], []).build()
    trade = {'payload': {'tradeID': 'T', 'conid': 'o'}, 'currency': 'USD', 'quantity': Decimal('1'),
             'price': Decimal('0'), 'side': 'BUY', 'instrument_id': 'instrument-id', 'event_id': 'event'}
    report['option_activity'] = account_option_activity([option()], [trade])
    rendered = context(report).eval('JSON.stringify(nodes.content)')
    assert '/ui/stocks/instrument-id' in rendered
    assert 'Assignment' in rendered


def test_concentration_ui_labels_derivative_weights():
    from app.analytics.account_holdings import account_concentration
    report = AccountInsights([], []).build()
    report['concentration'] = account_concentration([])
    rendered = context(report).eval('JSON.stringify(nodes.content)')
    assert 'Option weights do not measure underlying exposure' in rendered
    assert 'cash and accruals excluded' in rendered


def test_commission_ui_reports_coverage_and_signed_costs():
    from app.analytics.account_commissions import account_commissions
    from test_account_commissions import detail, trade
    from test_account_insights import row
    report = AccountInsights([], []).build()
    report['commissions'] = account_commissions([detail(), row('CashReport',
        fromDate='20260901', toDate='20260918')], [trade()])
    rendered = context(report).eval('JSON.stringify(nodes.content)')
    assert 'USD -3.00' in rendered
    assert '1 of 1 commissioned executions' in rendered
    assert 'already included in totals' in rendered
