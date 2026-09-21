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
