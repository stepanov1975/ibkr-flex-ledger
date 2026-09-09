"""Stock-family history dashboard, using the portfolio's existing visual style."""

STOCK_HISTORY_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stock history · IBKR Portfolio</title><style>
:root{color-scheme:dark;--panel:#151d31;--line:#2a3552;--text:#edf2ff;--muted:#9ba9c7;--accent:#68d5b4;--bad:#ff7c8b}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#0b1020,#111a2d);color:var(--text);font:15px system-ui,sans-serif}
header,main{max-width:1400px;margin:auto;padding:24px}header{display:flex;justify-content:space-between;align-items:center;gap:16px}h1{margin:0;font-size:24px}h2{font-size:16px;margin:0 0 14px}.muted{color:var(--muted)}a{color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 16px 45px #0004}.full{grid-column:1/-1}.metric{font-size:25px;font-weight:700;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--line)}th{color:var(--muted)}.number{text-align:right;white-space:nowrap}.scroll{overflow:auto;max-height:600px}.bad{color:var(--bad)}
button{background:#214f4a;color:var(--text);border:1px solid #327568;border-radius:8px;padding:9px 11px;cursor:pointer}button:hover{filter:brightness(1.15)}#history-error:empty,#history-state:empty{display:none}#history-error,#history-state{margin-bottom:16px}
@media(max-width:700px){.grid{grid-template-columns:1fr}header{align-items:flex-start;flex-direction:column}}
</style></head><body><header><div><h1 id="stock-name">Stock history</h1><div class="muted">Stock and related options · All imported history</div></div><div><a href="/ui">Back to portfolio</a> · <button onclick="loadHistory()">Refresh</button></div></header>
<main><div id="history-error" class="bad" role="alert"></div><div id="history-state" class="bad"></div><div class="grid">
<section class="card"><h2>Total realized P&amp;L</h2><div id="realized-pnl" class="metric">N/A</div></section>
<section class="card"><h2>Total unrealized P&amp;L</h2><div id="unrealized-pnl" class="metric">N/A</div></section>
<section class="card"><h2>Total P&amp;L</h2><div id="total-pnl" class="metric">N/A</div><div id="report-date" class="muted">Loading…</div></section>
<section class="card full"><h2>P&amp;L by instrument</h2><div class="scroll"><table><thead><tr><th>Instrument</th><th>As of</th><th class="number">Position</th><th class="number">Realized</th><th class="number">Unrealized</th><th class="number">Total</th></tr></thead><tbody id="positions"></tbody></table></div></section>
<section class="card full"><h2>Open and closed operations</h2><p class="muted">Each row is a FIFO opening lot. Realized P&amp;L includes partial closes and trading costs; unrealized P&amp;L covers the remaining position. Totals above also include recorded cashflows.</p><div id="lots-empty" class="muted"></div><div class="scroll"><table><thead><tr><th>Opened</th><th>Closed</th><th>Instrument</th><th>Status</th><th class="number">Opened quantity</th><th class="number">Remaining</th><th class="number">Realized P&amp;L</th><th class="number">Unrealized P&amp;L</th></tr></thead><tbody id="lots"></tbody></table></div></section>
<section class="card full"><h2>Activity history</h2><p class="muted">Every imported trade, cashflow, and corporate action, newest first. Prices and cash amounts are in the transaction currency.</p><div id="activity-empty" class="muted"></div><div class="scroll"><table><thead><tr><th>Date</th><th>Instrument</th><th>Type</th><th>Action</th><th class="number">Quantity</th><th class="number">Price</th><th class="number">Cash amount</th><th>Description</th></tr></thead><tbody id="activity"></tbody></table></div></section>
</div></main><script>
const el=id=>document.getElementById(id);
function cell(row,value,cls=''){const td=document.createElement('td');td.textContent=String(value??'');if(cls)td.className=cls;row.append(td)}
function formatCurrency(value,currency){if(value===null||value===undefined||!currency)return 'N/A';const amount=Number(value);if(!Number.isFinite(amount))return 'N/A';try{return new Intl.NumberFormat('en-US',{style:'currency',currency,currencyDisplay:'code',minimumFractionDigits:2,maximumFractionDigits:2}).format(amount)}catch{return 'N/A'}}
function formatQuantity(value){if(value===null||value===undefined)return 'N/A';return new Intl.NumberFormat('en-US',{maximumFractionDigits:8}).format(Number(value))}
function formatDate(value){const match=/^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(String(value??''));return match?`${match[3]}/${match[2]}/${match[1].slice(-2)}`:'N/A'}
function formatDateTime(value){if(!value)return 'N/A';const date=new Date(value);if(Number.isNaN(date.getTime()))return 'N/A';return new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Jerusalem',day:'2-digit',month:'2-digit',year:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(date).replace(',','')}
function renderHistory(report){
  el('stock-name').textContent=report.symbol;document.title=report.symbol+' · Stock history';
  el('report-date').textContent=report.report_date_local?'As of '+formatDate(report.report_date_local):'No snapshots';
  el('history-state').textContent=report.stale?'Provisional P&L: snapshots may not include all imported activity. Values are as of the dates shown below.':report.provisional?'Provisional report: some history or valuation data is incomplete or requires review. N/A means a reliable value is unavailable.':'';
  for(const [id,key] of [['realized-pnl','realized_pnl'],['unrealized-pnl','unrealized_pnl'],['total-pnl','total_pnl']])el(id).textContent=report.totals.map(total=>formatCurrency(total[key],total.currency)).join(' · ')||'N/A';
  for(const item of report.positions){const tr=document.createElement('tr');cell(tr,item.symbol+(item.provisional?' (Provisional)':''));cell(tr,formatDate(item.report_date_local));cell(tr,formatQuantity(item.position_qty),'number');for(const key of ['realized_pnl','unrealized_pnl','total_pnl'])cell(tr,formatCurrency(item[key],item.currency),'number');el('positions').append(tr)}
  for(const item of report.lots){const tr=document.createElement('tr');cell(tr,formatDateTime(item.opened_at_utc));cell(tr,formatDateTime(item.closed_at_utc));cell(tr,item.symbol);cell(tr,({open:'Open',partially_closed:'Partially closed',closed:'Closed'})[item.status]+(item.provisional?' (Provisional)':''));cell(tr,formatQuantity(item.open_quantity),'number');cell(tr,formatQuantity(item.remaining_quantity),'number');cell(tr,formatCurrency(item.realized_pnl,item.currency),'number');cell(tr,formatCurrency(item.unrealized_pnl,item.currency),'number');el('lots').append(tr)}
  el('lots-empty').textContent=report.lots.length?'':'No imported opening lots. See positions above for any broker-reported holdings.';
  for(const item of report.activity){const tr=document.createElement('tr');cell(tr,item.timestamp_utc?formatDateTime(item.timestamp_utc):formatDate(item.report_date_local));cell(tr,item.symbol);cell(tr,({trade:'Trade',cashflow:'Cashflow',corporate_action:'Corporate action'})[item.event_type]);cell(tr,item.action);cell(tr,formatQuantity(item.quantity),'number');cell(tr,formatCurrency(item.price,item.currency),'number');cell(tr,formatCurrency(item.amount,item.currency),'number');cell(tr,item.description);el('activity').append(tr)}
  el('activity-empty').textContent=report.activity.length?'':'No imported activity for this stock or its options.';
}
async function loadHistory(){
  el('history-error').textContent='';el('history-state').textContent='';el('report-date').textContent='Loading…';
  for(const id of ['realized-pnl','unrealized-pnl','total-pnl'])el(id).textContent='N/A';
  for(const id of ['positions','lots','activity'])el(id).replaceChildren();
  for(const id of ['lots-empty','activity-empty'])el(id).textContent='';
  try{const response=await fetch('/reports/stock-history/__INSTRUMENT_ID__');const report=await response.json();if(!response.ok)throw new Error(report.message||response.statusText);renderHistory(report)}catch(error){el('history-error').textContent=error.message||'Could not load stock history';el('report-date').textContent='N/A'}
}
loadHistory();
</script></body></html>"""
