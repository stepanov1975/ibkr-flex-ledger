"""Paginated deposit and withdrawal history page."""


TRANSFER_HISTORY_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Transfer history · IBKR Flex Ledger</title><style>
:root{color-scheme:dark;--panel:#151d31;--line:#2a3552;--text:#edf2ff;--muted:#9ba9c7;--accent:#68d5b4;--bad:#ff7c8b}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#0b1020,#111a2d);color:var(--text);font:15px system-ui,sans-serif}
header,main{max-width:1200px;margin:auto;padding:24px}header{display:flex;justify-content:space-between;align-items:center;gap:16px}h1{margin:0;font-size:24px}.muted{color:var(--muted)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 16px 45px #0004}
button{background:#214f4a;color:var(--text);border:1px solid #327568;border-radius:8px;padding:9px 11px;cursor:pointer}button:hover:not(:disabled){filter:brightness(1.15)}button:disabled{opacity:.45;cursor:default}a{color:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--line)}th{color:var(--muted)}.number{text-align:right}.scroll{overflow:auto}.bad{color:var(--bad)}
.pagination{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-top:18px}.pagination div{display:flex;gap:8px}
@media(max-width:650px){header{align-items:flex-start;flex-direction:column}header,main{padding:16px}}
</style></head><body><header><div><h1>Transfer history</h1><div class="muted">All deposits and withdrawals · Newest first · Original currency</div></div><div><a href="/ui">Portfolio</a> · <button id="refresh-transfers">Refresh</button></div></header>
<main><section class="card" aria-label="Transfer history">
<p id="transfers-error" class="bad" role="alert"></p>
<div class="scroll"><table><thead><tr><th>Date</th><th>Type</th><th class="number">Amount</th><th>Currency</th><th>Description</th></tr></thead><tbody id="transfers"></tbody></table></div>
<nav class="pagination" aria-label="Transfer history pagination"><span id="transfers-summary" class="muted" aria-live="polite">Loading transfers…</span><div><button id="previous-page" disabled>Previous</button><button id="next-page" disabled>Next</button></div></nav>
</section></main><script>
const el=id=>document.getElementById(id);const esc=value=>String(value??'');
function cell(row,value,cls=''){const td=document.createElement('td');td.textContent=esc(value);if(cls)td.className=cls;row.append(td)}
function formatCurrency(value,currency){if(value===null||value===undefined)return 'N/A';const amount=Number(value);if(!Number.isFinite(amount)||!currency)return esc(value);try{return new Intl.NumberFormat('en-US',{style:'currency',currency,currencyDisplay:'code',minimumFractionDigits:2,maximumFractionDigits:2}).format(amount)}catch{return esc(value)}}
function formatDate(value){const match=/^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(String(value??''));return match?`${match[3]}/${match[2]}/${match[1].slice(-2)}`:'—'}
let offset=0,pageSize=25,hasMore=false,loaded=false,loading=false;
async function loadTransfers(nextOffset=offset){
if(loading)return;
loading=true;el('transfers-error').textContent='';
for(const id of ['previous-page','next-page','refresh-transfers'])el(id).disabled=true;
try{
let x;
while(true){
const response=await fetch('/reports/transfer-history?limit=25&offset='+nextOffset);
x=await response.json();if(!response.ok)throw new Error(x?.message||x?.code||response.statusText);
if(nextOffset===0||x.items.length>0)break;
nextOffset=Math.max(0,Math.ceil(x.page.total/x.page.applied_limit)-1)*x.page.applied_limit;
}
el('transfers').replaceChildren();
for(const item of x.items){const tr=document.createElement('tr');cell(tr,formatDate(item.report_date_local));cell(tr,item.type);cell(tr,formatCurrency(item.amount,item.currency),'number');cell(tr,item.currency);cell(tr,item.description||'');el('transfers').append(tr)}
offset=x.page.offset;pageSize=x.page.applied_limit;hasMore=x.page.has_more;loaded=true;
el('transfers-summary').textContent=x.page.total===0?'No transfers yet.':`${offset+1}–${offset+x.page.returned} of ${x.page.total} transfers · Page ${Math.floor(offset/pageSize)+1} of ${Math.ceil(x.page.total/pageSize)}`;
}catch(error){el('transfers-error').textContent=error.message;if(!loaded)el('transfers-summary').textContent=''}
finally{loading=false;el('previous-page').disabled=offset===0;el('next-page').disabled=!hasMore;el('refresh-transfers').disabled=false}
}
el('previous-page').onclick=()=>loadTransfers(Math.max(0,offset-pageSize));
el('next-page').onclick=()=>loadTransfers(offset+pageSize);
el('refresh-transfers').onclick=()=>loadTransfers();
loadTransfers();
</script></body></html>"""
