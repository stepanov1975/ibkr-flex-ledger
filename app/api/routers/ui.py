"""Small dependency-free operations and portfolio dashboard."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


def api_create_ui_router() -> APIRouter:
    """Create the browser dashboard route."""

    router = APIRouter(tags=["ui"])

    @router.get("/ui", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(_DASHBOARD_HTML)

    return router


_DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IBKR Flex Ledger</title><style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#151d31;--line:#2a3552;--text:#edf2ff;--muted:#9ba9c7;--accent:#68d5b4;--bad:#ff7c8b}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#0b1020,#111a2d);color:var(--text);font:15px system-ui,sans-serif}
header,main{max-width:1200px;margin:auto;padding:24px}header{display:flex;justify-content:space-between;align-items:center}h1{margin:0;font-size:24px}h2{font-size:16px;margin:0 0 14px}.muted{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:16px}.card{grid-column:span 4;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 16px 45px #0004}.wide{grid-column:span 8}.full{grid-column:1/-1}
.metric{font-size:28px;font-weight:700;margin-top:8px}button,input{background:#0e1527;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px 11px}button{cursor:pointer;background:#214f4a;border-color:#327568}button:hover{filter:brightness(1.15)}form{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--line)}th{color:var(--muted)}.scroll{overflow:auto;max-height:430px}.pill{padding:3px 7px;border-radius:999px;background:#26324d}.bad{color:var(--bad)}a{color:var(--accent)}
@media(max-width:850px){.card,.wide{grid-column:1/-1}header{align-items:flex-start;gap:12px;flex-direction:column}}
</style></head><body><header><div><h1>IBKR Flex Ledger</h1><div class="muted">Auditable portfolio accounting</div></div><div><a href="/docs">API docs</a> · <button onclick="loadAll()">Refresh</button></div></header>
<main><div class="grid">
<section class="card"><h2>Ingestion success</h2><div id="success" class="metric">—</div><div id="slo-note" class="muted">Loading SLO…</div></section>
<section class="card"><h2>Open manual cases</h2><div id="case-count" class="metric">—</div><div class="muted">Affected instruments are provisional</div></section>
<section class="card"><h2>Latest portfolio P&amp;L</h2><div id="total-pnl" class="metric">—</div><div class="muted">Functional currency snapshots</div></section>
<section class="card wide"><h2>Daily P&amp;L by instrument</h2><div class="scroll"><table><thead><tr><th>Date</th><th>Symbol</th><th>Position</th><th>Realized</th><th>Unrealized</th><th>Total</th><th>State</th></tr></thead><tbody id="pnl"></tbody></table></div></section>
<section class="card"><h2>Labels</h2><form id="label-form"><input id="label-name" required placeholder="New label"><input id="label-color" placeholder="#68d5b4"><button>Add</button></form><div id="labels"></div></section>
<section class="card full"><h2>Corporate-action review queue</h2><div class="scroll"><table><thead><tr><th>Created</th><th>Symbol</th><th>Action</th><th>Status</th><th>Owner</th><th></th></tr></thead><tbody id="cases"></tbody></table></div></section>
<section class="card full"><h2>Recent ingestion runs</h2><div class="scroll"><table><thead><tr><th>Started</th><th>Type</th><th>Status</th><th>Duration</th><th>Error</th></tr></thead><tbody id="runs"></tbody></table></div></section>
</div></main><script>
const el=id=>document.getElementById(id); const esc=value=>String(value??'');
function cell(row,value,cls=''){const td=document.createElement('td');td.textContent=esc(value);if(cls)td.className=cls;row.append(td)}
function formatCurrency(value,currency){const amount=Number(value);if(!Number.isFinite(amount)||!currency)return esc(value);try{return new Intl.NumberFormat('en-US',{style:'currency',currency,currencyDisplay:'code',minimumFractionDigits:2,maximumFractionDigits:2}).format(amount)}catch{return esc(value)}}
function formatPosition(value){const amount=Number(value);return Number.isFinite(amount)?new Intl.NumberFormat('en-US',{minimumFractionDigits:0,maximumFractionDigits:3}).format(amount):esc(value)}
async function json(url,options){const response=await fetch(url,options);const data=response.status===204?null:await response.json();if(!response.ok)throw new Error(data?.message||data?.code||response.statusText);return data}
async function loadSlo(){const x=await json('/operations/slo');el('success').textContent=x.success_rate===null?'No runs':(x.success_rate*100).toFixed(1)+'%';el('slo-note').textContent=x.alerting?'Attention required':'Within alert thresholds';el('slo-note').className=x.alerting?'bad':'muted'}
async function loadPnl(){const x=await json('/reports/pnl/by-instrument');el('pnl').replaceChildren();let latest=null,total=0;for(const item of x.items){if(latest===null||item.report_date_local>latest){latest=item.report_date_local;total=0}if(item.report_date_local===latest)total+=Number(item.total_pnl);const tr=document.createElement('tr');[item.report_date_local,item.symbol].forEach(v=>cell(tr,v));cell(tr,formatPosition(item.position_qty));cell(tr,formatCurrency(item.realized_pnl,item.currency));cell(tr,formatCurrency(item.unrealized_pnl,item.currency));cell(tr,formatCurrency(item.total_pnl,item.currency));cell(tr,item.provisional?'Provisional':'Final',item.provisional?'bad':'');el('pnl').append(tr)}el('total-pnl').textContent=latest===null?'No snapshots':total.toFixed(2)}
async function loadLabels(){const x=await json('/labels');el('labels').replaceChildren();for(const item of x.items){const p=document.createElement('p');p.className='pill';p.textContent=item.name;el('labels').append(p)}}
async function loadCases(){const x=await json('/corporate-actions/cases?status=open');el('case-count').textContent=x.items.length;el('cases').replaceChildren();for(const item of x.items){const tr=document.createElement('tr');[item.created_at_utc.slice(0,10),item.symbol,item.action_type,item.status,item.owner||'Unassigned'].forEach(v=>cell(tr,v));const td=document.createElement('td'),button=document.createElement('button');button.textContent='Resolve';button.onclick=()=>resolveCase(item.case_id);td.append(button);tr.append(td);el('cases').append(tr)}}
async function resolveCase(id){const note=prompt('Resolution note');if(!note)return;await json('/corporate-actions/cases/'+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:'resolved',owner:'app owner',resolution_note:note})});await loadAll()}
async function loadRuns(){const x=await json('/ingestion/runs?limit=20');el('runs').replaceChildren();for(const item of x.items){const tr=document.createElement('tr');[item.started_at_utc,item.run_type,item.status,item.duration_ms??'—',item.error_message??''].forEach(v=>cell(tr,v,item.status==='failed'?'bad':''));el('runs').append(tr)}}
el('label-form').onsubmit=async event=>{event.preventDefault();await json('/labels',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:el('label-name').value,color:el('label-color').value||null})});event.target.reset();await loadLabels()};
async function loadAll(){for(const task of [loadSlo,loadPnl,loadLabels,loadCases,loadRuns]){try{await task()}catch(error){console.error(error)}}}loadAll();
</script></body></html>"""
