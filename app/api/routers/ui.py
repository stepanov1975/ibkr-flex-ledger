"""Small dependency-free operations and portfolio dashboard."""

from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .ingestion_runs_ui import INGESTION_RUNS_HTML
from .stock_history_ui import STOCK_HISTORY_HTML
from .transfer_history_ui import TRANSFER_HISTORY_HTML


def api_create_ui_router() -> APIRouter:
    """Create the portfolio and operations dashboard routes."""

    router = APIRouter(tags=["ui"])

    @router.get("/ui", response_class=HTMLResponse)
    def portfolio_dashboard() -> HTMLResponse:
        return HTMLResponse(_PORTFOLIO_DASHBOARD_HTML)

    @router.get("/ui/stocks/{instrument_id}", response_class=HTMLResponse)
    def stock_history_dashboard(instrument_id: UUID) -> HTMLResponse:
        return HTMLResponse(STOCK_HISTORY_HTML.replace("__INSTRUMENT_ID__", str(instrument_id)))

    @router.get("/ui/costs", response_class=HTMLResponse)
    def costs_dashboard() -> HTMLResponse:
        return HTMLResponse(_COSTS_DASHBOARD_HTML)

    @router.get("/ui/transfers", response_class=HTMLResponse)
    def transfer_history_dashboard() -> HTMLResponse:
        return HTMLResponse(TRANSFER_HISTORY_HTML)

    @router.get("/ui/operations", response_class=HTMLResponse)
    def operations_dashboard() -> HTMLResponse:
        return HTMLResponse(_OPERATIONS_DASHBOARD_HTML)

    @router.get("/ui/ingestion-runs", response_class=HTMLResponse)
    def ingestion_runs_dashboard() -> HTMLResponse:
        return HTMLResponse(INGESTION_RUNS_HTML)

    return router


_PORTFOLIO_DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IBKR Portfolio</title><style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#151d31;--line:#2a3552;--text:#edf2ff;--muted:#9ba9c7;--accent:#68d5b4;--bad:#ff7c8b}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#0b1020,#111a2d);color:var(--text);font:15px system-ui,sans-serif}
header,main{max-width:1400px;margin:auto;padding:24px}header{display:flex;justify-content:space-between;align-items:center}h1{margin:0;font-size:24px}h2{font-size:16px;margin:0 0 14px}.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}.card{grid-column:span 3;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 16px 45px #0004}.half{grid-column:span 6}.full{grid-column:1/-1}
.metric{font-size:25px;font-weight:700;margin-top:8px}button{background:#214f4a;color:var(--text);border:1px solid #327568;border-radius:8px;padding:9px 11px;cursor:pointer}button:hover{filter:brightness(1.15)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--line)}th{color:var(--muted)}.number{text-align:right}.scroll{overflow:auto;max-height:520px}.bad{color:var(--bad)}a{color:var(--accent)}
.section-heading{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:14px}.section-heading h2{margin:0}.toggle{display:flex;align-items:center;gap:7px;color:var(--muted);white-space:nowrap}.toggle input{accent-color:var(--accent)}
@media(max-width:1000px){.card{grid-column:span 6}.half{grid-column:1/-1}}@media(max-width:650px){.card{grid-column:1/-1}header{align-items:flex-start;gap:12px;flex-direction:column}}
</style></head><body><header><div><h1>IBKR Portfolio</h1><div class="muted">Latest available IBKR data</div></div><div><a href="/ui/costs">Costs</a> · <a href="/ui/operations">Operations</a> · <a href="/docs">API docs</a> · <button onclick="loadAll()">Refresh</button></div></header>
<main><div class="grid">
<section class="card"><h2>Latest portfolio P&amp;L</h2><div id="total-pnl" class="metric">—</div><div id="pnl-report-date" class="muted">Loading…</div><div id="pnl-state" class="muted"></div></section>
<section class="card"><h2>Realized</h2><div id="realized-pnl" class="metric">—</div></section>
<section class="card"><h2>Unrealized</h2><div id="unrealized-pnl" class="metric">—</div></section>
<section class="card"><h2>Estimated net liquidation value</h2><div id="net-liquidation" class="metric">—</div><div id="valuation-report-date" class="muted">Loading…</div></section>
<section class="card"><h2>Net transfers (USD)</h2><div id="net-transfers-usd" class="metric">—</div></section>
<section class="card"><h2>Total profit (USD)</h2><div id="total-profit-usd" class="metric">—</div></section>
<section class="card"><h2>Return on net transfers</h2><div id="profit-percent" class="metric">—</div></section>
<section class="card"><h2>Total costs (USD)</h2><div id="total-costs-usd" class="metric">—</div><div id="cost-history-range" class="muted">Loading…</div></section>
<section class="card full"><div class="section-heading"><h2>Total P&amp;L by instrument</h2><label class="toggle"><input id="hide-zero-positions" type="checkbox" checked> Hide zero positions</label></div><div class="scroll"><table><thead><tr><th>Symbol</th><th>Position</th><th>Average cost</th><th>Total cost</th><th>Last-day value</th><th>Realized</th><th>Unrealized</th><th>Total</th></tr></thead><tbody id="pnl"></tbody></table></div></section>
<section class="card"><h2>Net dividend payments</h2><div id="net-dividend-payments-usd" class="metric">—</div><div class="muted">Gross <span id="gross-dividend-payments-usd">—</span> · Withholding <span id="dividend-withholding-tax-usd">—</span></div></section>
<section class="card half"><h2>Cash balances</h2><div class="scroll"><table><thead><tr><th>Currency</th><th class="number">Total cash</th></tr></thead><tbody id="cash-balances"></tbody></table></div></section>
<section class="card half"><div class="section-heading"><h2>Transfer summary by currency</h2><a href="/ui/transfers">Transfer history</a></div><div class="scroll"><table><thead><tr><th>Currency</th><th>Net transfers</th><th>Gross deposits</th><th>Gross withdrawals</th></tr></thead><tbody id="transfer-summary"></tbody></table></div></section>
</div></main><script>
const el=id=>document.getElementById(id);const esc=value=>String(value??'');
function cell(row,value,cls=''){const td=document.createElement('td');td.textContent=esc(value);if(cls)td.className=cls;row.append(td)}
function formatCurrency(value,currency){if(value===null||value===undefined)return 'N/A';const amount=Number(value);if(!Number.isFinite(amount)||!currency)return esc(value);try{return new Intl.NumberFormat('en-US',{style:'currency',currency,currencyDisplay:'code',minimumFractionDigits:2,maximumFractionDigits:2}).format(amount)}catch{return esc(value)}}
function formatPosition(value){const amount=Number(value);return Number.isFinite(amount)?new Intl.NumberFormat('en-US',{minimumFractionDigits:0,maximumFractionDigits:3}).format(amount):esc(value)}
function formatPercent(value){if(value===null||value===undefined)return 'N/A';const amount=Number(value);return Number.isFinite(amount)?new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}).format(amount)+'%':'N/A'}
function formatDate(value){const match=/^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(String(value??''));return match?`${match[3]}/${match[2]}/${match[1].slice(-2)}`:'—'}
async function json(url){const response=await fetch(url);const data=await response.json();if(!response.ok)throw new Error(data?.message||data?.code||response.statusText);return data}
let latestPnlItems=[];
function renderPnl(){el('pnl').replaceChildren();const hideZero=el('hide-zero-positions').checked;for(const item of latestPnlItems){if(hideZero&&Number(item.position_qty)===0)continue;const tr=document.createElement('tr');const symbolCell=document.createElement('td');const link=document.createElement('a');link.href='/ui/stocks/'+encodeURIComponent(item.instrument_id);link.textContent=item.symbol+(item.provisional?' (Provisional)':'');if(item.provisional)link.className='bad';symbolCell.append(link);tr.append(symbolCell);cell(tr,formatPosition(item.position_qty));cell(tr,formatCurrency(item.average_cost,item.currency));cell(tr,formatCurrency(item.total_cost,item.currency));cell(tr,formatCurrency(item.last_day_value,item.currency));cell(tr,formatCurrency(item.realized_pnl,item.currency));cell(tr,formatCurrency(item.unrealized_pnl,item.currency));cell(tr,formatCurrency(item.total_pnl,item.currency));el('pnl').append(tr)}}
async function loadPnl(){const x=await json('/reports/pnl/by-instrument');const latest=x.items.reduce((value,item)=>value===null||item.report_date_local>value?item.report_date_local:value,null);latestPnlItems=latest===null?[]:x.items.filter(item=>item.report_date_local===latest);let realized=0,unrealized=0,total=0;for(const item of latestPnlItems){realized+=Number(item.realized_pnl);unrealized+=Number(item.unrealized_pnl);total+=Number(item.total_pnl)}renderPnl();const provisional=latestPnlItems.some(item=>item.provisional);el('pnl-state').textContent=provisional?'Provisional totals — see Operations for details':'';el('pnl-state').className=provisional?'bad':'muted';for(const id of ['total-pnl','realized-pnl','unrealized-pnl'])el(id).className=provisional?'metric bad':'metric';const currency=latestPnlItems[0]?.currency||'USD';el('total-pnl').textContent=latest===null?'No snapshots':formatCurrency(total,currency);el('realized-pnl').textContent=latest===null?'No snapshots':formatCurrency(realized,currency);el('unrealized-pnl').textContent=latest===null?'No snapshots':formatCurrency(unrealized,currency);el('pnl-report-date').textContent=latest===null?'N/A':'As of '+formatDate(latest)}
async function loadSummary(){const x=await json('/reports/portfolio-summary');el('cash-balances').replaceChildren();for(const item of x.cash_balances){const tr=document.createElement('tr');cell(tr,item.currency);cell(tr,formatCurrency(item.amount,item.currency),'number');el('cash-balances').append(tr)}el('transfer-summary').replaceChildren();for(const item of x.transfer_summary_by_currency){const tr=document.createElement('tr');cell(tr,item.currency);cell(tr,formatCurrency(item.net_transfers,item.currency),'number');cell(tr,formatCurrency(item.gross_deposits,item.currency),'number');cell(tr,formatCurrency(item.gross_withdrawals,item.currency),'number');el('transfer-summary').append(tr)}el('net-liquidation').textContent=formatCurrency(x.estimated_net_liquidation_value_usd,'USD');el('net-transfers-usd').textContent=formatCurrency(x.net_transfers_usd,'USD');el('total-profit-usd').textContent=formatCurrency(x.total_profit_usd,'USD');el('profit-percent').textContent=formatPercent(x.profit_percent);el('total-costs-usd').textContent=formatCurrency(x.total_costs_usd,'USD');el('net-dividend-payments-usd').textContent=formatCurrency(x.net_dividend_payments_usd,'USD');el('gross-dividend-payments-usd').textContent=formatCurrency(x.gross_dividend_payments_usd,'USD');el('dividend-withholding-tax-usd').textContent=formatCurrency(x.dividend_withholding_tax_usd,'USD');el('cost-history-range').textContent=x.activity_date_from&&x.activity_date_to?'All history: '+formatDate(x.activity_date_from)+' – '+formatDate(x.activity_date_to):'No cost or dividend history';el('valuation-report-date').textContent=x.report_date_local?'As of '+formatDate(x.report_date_local):'N/A'}
el('hide-zero-positions').onchange=renderPnl;
async function loadAll(){for(const task of [loadPnl,loadSummary]){try{await task()}catch(error){console.error(error)}}}loadAll();
</script></body></html>"""


_COSTS_DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IBKR Portfolio Costs</title><style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#151d31;--line:#2a3552;--text:#edf2ff;--muted:#9ba9c7;--accent:#68d5b4}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#0b1020,#111a2d);color:var(--text);font:15px system-ui,sans-serif}
header,main{max-width:1200px;margin:auto;padding:24px}header{display:flex;justify-content:space-between;align-items:center}h1{margin:0;font-size:24px}h2{font-size:16px;margin:0 0 8px}.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}.card{grid-column:1/-1;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 16px 45px #0004}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:14px}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--line)}th{color:var(--muted)}.number{text-align:right}.scroll{overflow:auto;max-height:520px}tfoot th,tfoot td{color:var(--text);font-weight:700}button{background:#214f4a;color:var(--text);border:1px solid #327568;border-radius:8px;padding:9px 11px;cursor:pointer}button:hover{filter:brightness(1.15)}a{color:var(--accent)}
@media(max-width:650px){header{align-items:flex-start;gap:12px;flex-direction:column}}
</style></head><body><header><div><h1>Portfolio Costs</h1><div class="muted">All imported IBKR cost history</div></div><div><a href="/ui">Portfolio</a> · <a href="/ui/operations">Operations</a> · <a href="/docs">API docs</a> · <button onclick="loadCosts()">Refresh</button></div></header>
<main><div class="grid">
<section class="card"><h2>Securities commissions</h2><div id="securities-commission-coverage" class="muted">Loading…</div><div class="scroll"><table><thead><tr><th>Instrument type</th><th>Side</th><th>Executions</th><th>Commission</th></tr></thead><tbody id="securities-commission-summary"></tbody><tfoot><tr><th>Total buys</th><td></td><td id="buy-execution-total"></td><td id="buy-commission-total" class="number"></td></tr><tr><th>Total sells</th><td></td><td id="sell-execution-total"></td><td id="sell-commission-total" class="number"></td></tr><tr><th>Grand total</th><td></td><td id="securities-execution-total"></td><td id="securities-commission-total" class="number"></td></tr></tfoot></table></div></section>
<section class="card"><h2>Cost summary by category</h2><div id="cost-history-range" class="muted">Loading…</div><div class="scroll"><table><thead><tr><th>Category</th><th>Net cost</th><th>P&amp;L treatment</th></tr></thead><tbody id="cost-summary"></tbody></table></div><div class="muted">Costs outside instrument P&amp;L: <span id="costs-outside-pnl-usd">—</span></div></section>
</div></main><script>
const el=id=>document.getElementById(id);const esc=value=>String(value??'');
function cell(row,value,cls=''){const td=document.createElement('td');td.textContent=esc(value);if(cls)td.className=cls;row.append(td)}
function formatCurrency(value,currency){if(value===null||value===undefined)return 'N/A';const amount=Number(value);if(!Number.isFinite(amount)||!currency)return esc(value);try{return new Intl.NumberFormat('en-US',{style:'currency',currency,currencyDisplay:'code',minimumFractionDigits:2,maximumFractionDigits:2}).format(amount)}catch{return esc(value)}}
function formatDate(value){const match=/^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(String(value??''));return match?`${match[3]}/${match[2]}/${match[1].slice(-2)}`:'—'}
async function json(url){const response=await fetch(url);const data=await response.json();if(!response.ok)throw new Error(data?.message||data?.code||response.statusText);return data}
function sideTotals(items,side){const selected=items.filter(item=>item.side===side);return{executions:selected.reduce((total,item)=>total+Number(item.execution_count),0),commission:selected.some(item=>item.commission_usd===null)?null:selected.reduce((total,item)=>total+Number(item.commission_usd),0)}}
async function loadCosts(){const x=await json('/reports/portfolio-summary');const order={Stocks:0,Options:1};const items=[...x.securities_commission_summary].sort((a,b)=>(order[a.instrument_type]??99)-(order[b.instrument_type]??99)||a.instrument_type.localeCompare(b.instrument_type)||a.side.localeCompare(b.side));el('securities-commission-summary').replaceChildren();for(const item of items){const tr=document.createElement('tr');cell(tr,item.instrument_type);cell(tr,item.side==='BUY'?'Buy':'Sell');cell(tr,item.execution_count,'number');cell(tr,formatCurrency(item.commission_usd,'USD'),'number');el('securities-commission-summary').append(tr)}const buys=sideTotals(items,'BUY'),sells=sideTotals(items,'SELL');el('buy-execution-total').textContent=buys.executions;el('buy-commission-total').textContent=formatCurrency(buys.commission,'USD');el('sell-execution-total').textContent=sells.executions;el('sell-commission-total').textContent=formatCurrency(sells.commission,'USD');el('securities-execution-total').textContent=x.securities_commission_execution_count;el('securities-commission-total').textContent=formatCurrency(x.securities_commission_total_usd,'USD');el('securities-commission-coverage').textContent=x.securities_commission_date_from&&x.securities_commission_date_to?x.securities_commission_execution_count+' executions · '+x.securities_commission_instrument_count+' instruments · '+formatDate(x.securities_commission_date_from)+' – '+formatDate(x.securities_commission_date_to):'No securities commissions';el('cost-summary').replaceChildren();for(const item of x.cost_summary){const tr=document.createElement('tr');cell(tr,item.category);cell(tr,formatCurrency(item.net_cost_usd,'USD'),'number');cell(tr,item.included_in_instrument_pnl?'Included':'Outside');el('cost-summary').append(tr)}el('costs-outside-pnl-usd').textContent=formatCurrency(x.costs_outside_instrument_pnl_usd,'USD');el('cost-history-range').textContent=x.activity_date_from&&x.activity_date_to?'All history: '+formatDate(x.activity_date_from)+' – '+formatDate(x.activity_date_to):'No cost history'}
loadCosts().catch(error=>console.error(error));
</script></body></html>"""


_OPERATIONS_DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IBKR Flex Ledger</title><style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#151d31;--line:#2a3552;--text:#edf2ff;--muted:#9ba9c7;--accent:#68d5b4;--bad:#ff7c8b}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#0b1020,#111a2d);color:var(--text);font:15px system-ui,sans-serif}
header,main{max-width:1200px;margin:auto;padding:24px}header{display:flex;justify-content:space-between;align-items:center}h1{margin:0;font-size:24px}h2{font-size:16px;margin:0 0 14px}.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}.card{grid-column:span 4;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 16px 45px #0004}.wide{grid-column:span 8}.full{grid-column:1/-1}
.metric{font-size:28px;font-weight:700;margin-top:8px}button,input{background:#0e1527;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px 11px}button{cursor:pointer;background:#214f4a;border-color:#327568}button:hover{filter:brightness(1.15)}form{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--line)}th{color:var(--muted)}.scroll{overflow:auto;max-height:430px}.pill{padding:3px 7px;border-radius:999px;background:#26324d}.bad{color:var(--bad)}a{color:var(--accent)}
@media(max-width:850px){.card,.wide{grid-column:1/-1}header{align-items:flex-start;gap:12px;flex-direction:column}}
</style></head><body><header><div><h1>IBKR Flex Ledger</h1><div class="muted">Auditable portfolio accounting</div></div><div><a href="/ui">Portfolio</a> · <a href="/docs">API docs</a> · <button onclick="loadAll()">Refresh</button></div></header>
<main><div class="grid">
<section class="card"><h2>Scheduled ingestion success</h2><div id="success" class="metric">—</div><div id="slo-note" class="muted">Loading SLO…</div></section>
<section class="card"><h2>Actions needing attention</h2><div id="case-count" class="metric">—</div><div class="muted">Available decisions · <span id="unsupported-count">—</span> need accounting support</div></section>
<section class="card"><h2>Latest portfolio P&amp;L</h2><div id="total-pnl" class="metric">—</div><div class="muted">Functional currency snapshots</div></section>
<section class="card wide"><h2>Daily P&amp;L by instrument</h2><div class="scroll"><table><thead><tr><th>Date</th><th>Symbol</th><th>Position</th><th>Realized</th><th>Unrealized</th><th>Total</th><th>State</th></tr></thead><tbody id="pnl"></tbody></table></div></section>
<section class="card"><h2>Labels</h2><form id="label-form"><input id="label-name" required placeholder="New label"><input id="label-color" placeholder="#68d5b4"><button>Add</button></form><div id="labels"></div></section>
<section class="card full"><h2>Corporate-action review queue</h2>
<p class="muted">Supported actions with an explicit broker split ratio are processed automatically during ingestion. Review missing split ratios, paired identifier changes and received security distributions below.</p>
<p class="muted">Preview verifies the proposed treatment without saving. Apply saves the treatment and updates portfolio accounting.</p>
<label><input id="show-reviewed" type="checkbox"> Show handled actions</label>
<p id="case-error" class="bad" role="alert"></p>
<p id="empty-cases" hidden>No actions need a decision. Any unsupported accounting is listed separately below.</p>
<div class="scroll"><table><thead><tr><th>Created</th><th>Symbol</th><th>Action / report date</th><th>Reason and required check</th><th>Accounting status</th><th>Owner / review note</th><th></th></tr></thead><tbody id="cases"></tbody></table></div></section>
<section class="card full"><h2>Unsupported accounting</h2>
<p class="muted">These actions remain provisional and need accounting support. They are excluded from the decision count.</p>
<p id="empty-unsupported" hidden>No unsupported accounting actions.</p>
<div class="scroll"><table><thead><tr><th>Created</th><th>Symbol</th><th>Action / report date</th><th>Reason and required check</th><th>Accounting status</th><th>Owner / review note</th><th></th></tr></thead><tbody id="unsupported-cases"></tbody></table></div></section>
<section class="card full" id="split-editor" hidden><h2 id="split-title">Correct split ratio</h2>
<p id="split-description" class="muted"></p>
<p>Enter the share exchange from the broker notice: for a 3-for-2 split, enter 3 new shares and 2 old shares.</p>
<div><label>New shares <input id="new-shares" type="number" min="0" step="any"></label>
<label>Old shares <input id="old-shares" type="number" min="0" step="any"></label>
<label>Broker evidence / note <input id="split-note" maxlength="2000"></label></div>
<p><button id="preview-split">Preview changes</button> <button id="apply-split" disabled>Apply correction</button> <button id="cancel-split">Cancel</button></p>
<p id="split-summary" aria-live="polite"></p>
<div class="scroll"><table><thead><tr><th>Date</th><th>Comparison</th><th>Position</th><th>Cost basis</th><th>Realized</th><th>Unrealized</th><th>Total P&amp;L</th><th>State</th></tr></thead><tbody id="split-snapshots"></tbody></table></div>
<h3>Open FIFO lots</h3><p class="muted">Lot quantities and unit costs can change even when broker-reported positions stay the same.</p>
<div class="scroll"><table><thead><tr><th>Comparison</th><th>Lot</th><th>Remaining units</th><th>Unit cost</th><th>Lot cost basis</th></tr></thead><tbody id="split-lots"></tbody></table></div>
</section>
<section class="card full" id="resolution-editor" hidden><h2 id="resolution-title">Review corporate action</h2>
<p id="resolution-description"></p>
<h3>Broker legs</h3><div class="scroll"><table><thead><tr><th>Symbol</th><th>Conid</th><th>Quantity</th><th>Currency</th><th>Reported cost basis</th><th>Report date</th><th>Description</th></tr></thead><tbody id="resolution-legs"></tbody></table></div>
<div id="distribution-basis-fields" hidden><p><label><span id="distribution-basis-label">Total cost basis of credited units</span> <input id="distribution-basis" type="number" min="0" step="any" required></label></p>
<p class="muted">This records the received security's cost basis. It does not reallocate cost basis from the parent security. Enter zero only when the broker evidence confirms zero basis.</p></div>
<p><label>Broker evidence / note (required) <input id="resolution-note" maxlength="2000" required></label></p>
<p id="resolution-error" class="bad" role="alert"></p>
<p><button id="preview-resolution">Preview changes</button> <button id="apply-resolution" disabled>Apply treatment</button> <button id="cancel-resolution">Cancel</button></p>
<p id="resolution-summary" aria-live="polite"></p>
<div id="resolution-event-preview" hidden><h3>Event to approve</h3>
<div class="scroll"><table><thead><tr><th>IBKR action ID</th><th>Event date</th><th>Treatment</th><th>Security</th><th>Units</th><th>Total cost basis</th><th>Broker evidence / note</th></tr></thead><tbody id="resolution-event"></tbody></table></div>
</div>
</section>
<section class="card full"><h2>Recent ingestion runs</h2><div class="scroll"><table><thead><tr><th>Started</th><th>Type</th><th>Status</th><th>Duration</th><th>Error</th></tr></thead><tbody id="runs"></tbody></table></div><p><a href="/ui/ingestion-runs">View all ingestion runs</a></p></section>
</div></main><script>
const el=id=>document.getElementById(id); const esc=value=>String(value??'');
function cell(row,value,cls=''){const td=document.createElement('td');td.textContent=esc(value);if(cls)td.className=cls;row.append(td)}
function formatCurrency(value,currency){const amount=Number(value);if(!Number.isFinite(amount)||!currency)return esc(value);try{return new Intl.NumberFormat('en-US',{style:'currency',currency,currencyDisplay:'code',minimumFractionDigits:2,maximumFractionDigits:2}).format(amount)}catch{return esc(value)}}
function formatPosition(value){const amount=Number(value);return Number.isFinite(amount)?new Intl.NumberFormat('en-US',{minimumFractionDigits:0,maximumFractionDigits:3}).format(amount):esc(value)}
const uiDateTimeFormatter=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Jerusalem',day:'2-digit',month:'2-digit',year:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
function formatDate(value){const match=/^(\\d{4})-(\\d{2})-(\\d{2})$/.exec(String(value??''));return match?`${match[3]}/${match[2]}/${match[1].slice(-2)}`:'—'}
function formatDateTime(value){if(!value)return '—';const timestamp=new Date(value);return Number.isNaN(timestamp.getTime())?'—':uiDateTimeFormatter.format(timestamp).replace(',','')}
async function json(url,options){const response=await fetch(url,options);const data=response.status===204?null:await response.json();if(!response.ok)throw new Error(data?.message||data?.code||response.statusText);return data}
async function loadSlo(){const x=await json('/operations/slo');el('success').textContent=x.success_rate===null?'No scheduled runs':(x.success_rate*100).toFixed(1)+'%';el('slo-note').textContent=x.alerting?'Attention required':'Within alert thresholds';el('slo-note').className=x.alerting?'bad':'muted'}
async function loadPnl(){const x=await json('/reports/pnl/by-instrument');el('pnl').replaceChildren();let latest=null,total=0;for(const item of x.items){if(latest===null||item.report_date_local>latest){latest=item.report_date_local;total=0}if(item.report_date_local===latest)total+=Number(item.total_pnl);const tr=document.createElement('tr');[formatDate(item.report_date_local),item.symbol].forEach(v=>cell(tr,v));cell(tr,formatPosition(item.position_qty));cell(tr,formatCurrency(item.realized_pnl,item.currency));cell(tr,formatCurrency(item.unrealized_pnl,item.currency));cell(tr,formatCurrency(item.total_pnl,item.currency));cell(tr,item.provisional?'Provisional':'Final',item.provisional?'bad':'');el('pnl').append(tr)}el('total-pnl').textContent=latest===null?'No snapshots':total.toFixed(2)}
async function loadLabels(){const x=await json('/labels');el('labels').replaceChildren();for(const item of x.items){const p=document.createElement('p');p.className='pill';p.textContent=item.name;el('labels').append(p)}}
function reviewState(item){return item.review_state||(item.can_correct_split?'actionable':item.requires_manual?'unsupported':'handled')}
async function loadCases(){
const x=await json('/corporate-actions/cases'),actionable=x.items.filter(item=>reviewState(item)==='actionable').length,unsupported=x.items.filter(item=>reviewState(item)==='unsupported').length;
el('case-error').textContent='';el('case-count').textContent=actionable;el('unsupported-count').textContent=unsupported;el('empty-cases').hidden=actionable!==0;el('empty-unsupported').hidden=unsupported!==0;el('cases').replaceChildren();el('unsupported-cases').replaceChildren();
for(const item of x.items){
const state=reviewState(item);if(state==='handled'&&!el('show-reviewed').checked)continue;
const tr=document.createElement('tr');cell(tr,formatDateTime(item.created_at_utc));cell(tr,item.symbol);
cell(tr,item.action_type+' · '+formatDate(item.report_date_local)+' · '+(item.description||'No broker description'));
cell(tr,(item.review_reason||'')+' '+(item.required_check||''));
cell(tr,state==='actionable'?'Decision available · Provisional':state==='unsupported'?'Accounting support required · Provisional':'Handled');
cell(tr,(item.owner||'Unassigned')+(item.resolution_note?' · '+item.resolution_note:''));
const td=document.createElement('td');if(state==='actionable'){if(item.can_correct_split){const button=document.createElement('button');button.textContent='Enter split ratio';button.onclick=()=>openSplit(item);td.append(button)}for(const option of item.resolution_options||[]){if(!['security_transfer','distribution'].includes(option.type))continue;const button=document.createElement('button');button.textContent=option.type==='security_transfer'?'Preview transfer':'Enter distribution basis';button.onclick=()=>openResolution(item,option.type);td.append(button)}}tr.append(td);el(state==='unsupported'?'unsupported-cases':'cases').append(tr)
}}
let splitCase=null,splitPreview=null,splitDraft=null,splitVersion=0,splitBusy=false;
function invalidateSplit(){splitVersion++;splitPreview=null;splitDraft=null;el('apply-split').disabled=true;el('split-summary').textContent='';el('split-snapshots').replaceChildren();el('split-lots').replaceChildren()}
function openSplit(item){if(splitBusy||resolutionBusy)return;cancelResolution();splitCase=item;invalidateSplit();el('case-error').textContent='';el('split-editor').hidden=false;el('split-title').textContent='Correct '+item.symbol+' split ratio';el('split-description').textContent=(item.description||'')+' · Effective report date: '+formatDate(item.report_date_local);for(const id of ['new-shares','old-shares','split-note'])el(id).value=''}
function cancelSplit(){if(splitBusy)return;splitCase=null;invalidateSplit();el('split-editor').hidden=true}
function formatSplitPosition(value){
const raw=esc(value),match=/^(-?)([0-9]+)(?:[.]([0-9]*))?(?:e([+-]?[0-9]+))?$/i.exec(raw);if(!match)return raw;
const digits=match[2]+(match[3]||''),point=match[2].length+Number(match[4]||0);
const fixed=point<=0?'0.'+'0'.repeat(-point)+digits:point>=digits.length?digits.padEnd(point,'0'):digits.slice(0,point)+'.'+digits.slice(point);
const [whole,fractional='']=fixed.split('.'),fraction=fractional.replace(/0+$/,'');return match[1]+whole+(fraction?'.'+fraction:'');
}
function renderSplitPreview(result){
el('split-summary').textContent='Preview only — no changes saved. Ratio: '+result.factor+'. '+result.snapshots.length+' snapshot(s). Apply only after checking these changes against the broker statement.';
for(const item of result.snapshots){for(const side of ['before','after']){const values=item[side],tr=document.createElement('tr');cell(tr,formatDate(item.report_date_local));cell(tr,side==='before'?'Before':'After');cell(tr,formatSplitPosition(values.position_qty));for(const key of ['cost_basis','realized_pnl','unrealized_pnl','total_pnl'])cell(tr,values[key]===null?'N/A':formatCurrency(values[key],item.currency));cell(tr,values.provisional?'Provisional':'Final');el('split-snapshots').append(tr)}}
for(const side of ['before','after']){result['lots_'+side].forEach((lot,index)=>{const tr=document.createElement('tr');[side==='before'?'Before':'After',index+1,formatSplitPosition(lot.remaining_quantity),lot.unit_basis,lot.cost_basis_open].forEach(v=>cell(tr,v));el('split-lots').append(tr)})}}
async function requestSplit(apply){
if(!splitCase||splitBusy||(apply&&!splitPreview))return;
el('case-error').textContent='';if(!apply)invalidateSplit();
const draft=apply?splitDraft:{new_shares:el('new-shares').value,old_shares:el('old-shares').value,note:el('split-note').value.trim()};
if(!draft.note||![draft.new_shares,draft.old_shares].every(v=>Number.isFinite(Number(v))&&Number(v)>0)){el('case-error').textContent='Enter positive new and old share quantities and the broker evidence.';return}
const version=splitVersion,body=apply?{...draft,preview_token:splitPreview.preview_token}:draft;
splitBusy=true;for(const id of ['new-shares','old-shares','split-note','preview-split','apply-split','cancel-split'])el(id).disabled=true;
try{const result=await json('/corporate-actions/cases/'+splitCase.case_id+'/split/'+(apply?'apply':'preview'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(version!==splitVersion)return;if(apply){splitCase=null;invalidateSplit();el('split-editor').hidden=true;await loadAll()}else{splitDraft=draft;splitPreview=result;renderSplitPreview(result)}}catch(error){invalidateSplit();el('case-error').textContent=error.message}finally{splitBusy=false;for(const id of ['new-shares','old-shares','split-note','preview-split','cancel-split'])el(id).disabled=false;el('apply-split').disabled=!splitPreview}
}
for(const id of ['new-shares','old-shares','split-note'])el(id).oninput=invalidateSplit;
el('preview-split').onclick=()=>requestSplit(false);el('apply-split').onclick=()=>requestSplit(true);el('cancel-split').onclick=cancelSplit;
let resolutionCase=null,resolutionTreatment=null,resolutionPreview=null,resolutionDraft=null,resolutionVersion=0,resolutionBusy=false;
function invalidateResolution(){resolutionVersion++;resolutionPreview=null;resolutionDraft=null;el('apply-resolution').disabled=true;el('resolution-summary').textContent='';el('resolution-event-preview').hidden=true;el('resolution-event').replaceChildren()}
function cancelResolution(){if(resolutionBusy)return;resolutionCase=null;invalidateResolution();el('resolution-editor').hidden=true}
function openResolution(item,treatment){
if(splitBusy||resolutionBusy)return;cancelSplit();resolutionCase=item;resolutionTreatment=treatment;invalidateResolution();el('resolution-error').textContent='';el('resolution-editor').hidden=false;
el('resolution-title').textContent=(treatment==='security_transfer'?'Transfer existing position: ':'Record received security: ')+item.symbol;
el('resolution-description').textContent=[item.description,item.review_reason,item.required_check].filter(Boolean).join(' · ');
el('resolution-legs').replaceChildren();for(const leg of item.broker_legs){const tr=document.createElement('tr');[leg.symbol,leg.conid,formatSplitPosition(leg.quantity),leg.currency,leg.cost_basis===null?'N/A':formatCurrency(leg.cost_basis,leg.currency),formatDate(leg.report_date_local),leg.description].forEach(value=>cell(tr,value));el('resolution-legs').append(tr)}
el('distribution-basis-fields').hidden=treatment!=='distribution';el('distribution-basis').value='';el('resolution-note').value='';
if(treatment==='distribution'){const leg=item.broker_legs[0];el('distribution-basis-label').textContent='Total cost basis of all '+formatSplitPosition(leg.quantity)+' credited '+leg.symbol+' units ('+leg.currency+')'}
}
function renderResolutionPreview(result){
el('resolution-summary').textContent='Preview only — no changes saved. '+result.summary;
const event=result.event,transfer=result.treatment==='security_transfer',tr=document.createElement('tr');
[event.action_id,formatDate(event.report_date_local),transfer?'Security transfer':'Distribution',transfer?event.source_symbol+' → '+event.destination_symbol:event.destination_symbol,formatSplitPosition(event.quantity),transfer?'Carry existing FIFO basis':formatCurrency(event.cost_basis,event.currency),event.note].forEach(value=>cell(tr,value));
el('resolution-event').replaceChildren();el('resolution-event').append(tr);el('resolution-event-preview').hidden=false;
}
async function requestResolution(apply){
if(!resolutionCase||resolutionBusy||(apply&&!resolutionPreview))return;
el('resolution-error').textContent='';if(!apply)invalidateResolution();
const draft=apply?resolutionDraft:{treatment:resolutionTreatment,note:el('resolution-note').value.trim()};
if(!draft.note){el('resolution-error').textContent='Enter the broker evidence supporting this treatment.';return}
if(!apply&&resolutionTreatment==='distribution'){const basis=el('distribution-basis').value.trim();if(!basis||!Number.isFinite(Number(basis))||Number(basis)<0){el('resolution-error').textContent='Enter the total cost basis of the credited units in the displayed currency. Zero must be explicit.';return}draft.cost_basis=basis}
const version=resolutionVersion,body=apply?{...draft,preview_token:resolutionPreview.preview_token}:draft;
resolutionBusy=true;for(const id of ['distribution-basis','resolution-note','preview-resolution','apply-resolution','cancel-resolution'])el(id).disabled=true;
try{const result=await json('/corporate-actions/cases/'+resolutionCase.case_id+'/resolution/'+(apply?'apply':'preview'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(version!==resolutionVersion)return;if(apply){resolutionCase=null;invalidateResolution();el('resolution-editor').hidden=true;await loadAll()}else{resolutionDraft=draft;resolutionPreview=result;renderResolutionPreview(result)}}catch(error){invalidateResolution();el('resolution-error').textContent=error.message}finally{resolutionBusy=false;for(const id of ['distribution-basis','resolution-note','preview-resolution','cancel-resolution'])el(id).disabled=false;el('apply-resolution').disabled=!resolutionPreview}
}
for(const id of ['distribution-basis','resolution-note'])el(id).oninput=invalidateResolution;
el('preview-resolution').onclick=()=>requestResolution(false);el('apply-resolution').onclick=()=>requestResolution(true);el('cancel-resolution').onclick=cancelResolution;
el('show-reviewed').onchange=()=>loadCases().catch(error=>{el('case-error').textContent=error.message});
async function loadRuns(){const x=await json('/ingestion/runs?limit=5&sort_by=started_at_utc&sort_dir=desc');el('runs').replaceChildren();for(const item of x.items){const tr=document.createElement('tr');[formatDateTime(item.started_at_utc),item.run_type,item.status,item.duration_ms??'—',item.error_message??''].forEach(v=>cell(tr,v,item.status==='failed'?'bad':''));el('runs').append(tr)}}
el('label-form').onsubmit=async event=>{event.preventDefault();await json('/labels',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:el('label-name').value,color:el('label-color').value||null})});event.target.reset();await loadLabels()};
async function loadAll(){for(const task of [loadSlo,loadPnl,loadLabels,loadCases,loadRuns]){try{await task()}catch(error){if(task===loadCases)el('case-error').textContent=error.message;console.error(error)}}}loadAll();
</script></body></html>"""
