"""Paginated ingestion run history page."""


INGESTION_RUNS_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ingestion runs · IBKR Flex Ledger</title><style>
:root{color-scheme:dark;--panel:#151d31;--line:#2a3552;--text:#edf2ff;--muted:#9ba9c7;--accent:#68d5b4;--bad:#ff7c8b}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#0b1020,#111a2d);color:var(--text);font:15px system-ui,sans-serif}
header,main{max-width:1200px;margin:auto;padding:24px}header{display:flex;justify-content:space-between;align-items:center;gap:16px}h1{margin:0;font-size:24px}.muted{color:var(--muted)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 16px 45px #0004}
button{background:#214f4a;color:var(--text);border:1px solid #327568;border-radius:8px;padding:9px 11px;cursor:pointer}button:hover:not(:disabled){filter:brightness(1.15)}button:disabled{opacity:.45;cursor:default}a{color:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--line)}th{color:var(--muted)}.scroll{overflow:auto}.bad{color:var(--bad)}
.pagination{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-top:18px}.pagination div{display:flex;gap:8px}
@media(max-width:650px){header{align-items:flex-start;flex-direction:column}header,main{padding:16px}}
</style></head><body><header><div><h1>Ingestion runs</h1><div class="muted">All ingestion history · Newest first · Jerusalem time</div></div><div><a href="/ui">Portfolio</a> · <a href="/ui/operations">Operations</a> · <button id="refresh-runs">Refresh</button></div></header>
<main><section class="card" aria-label="Ingestion run history">
<p id="runs-error" class="bad" role="alert"></p>
<div class="scroll"><table><thead><tr><th>Started</th><th>Type</th><th>Status</th><th>Duration (ms)</th><th>Error</th></tr></thead><tbody id="runs"></tbody></table></div>
<nav class="pagination" aria-label="Ingestion runs pagination"><span id="runs-summary" class="muted" aria-live="polite">Loading ingestion runs…</span><div><button id="previous-page" disabled>Previous</button><button id="next-page" disabled>Next</button></div></nav>
</section></main><script>
const el=id=>document.getElementById(id);
function cell(row,value,cls=''){const td=document.createElement('td');td.textContent=String(value??'');if(cls)td.className=cls;row.append(td)}
const uiDateTimeFormatter=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Jerusalem',day:'2-digit',month:'2-digit',year:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
function formatDateTime(value){if(!value)return '—';const timestamp=new Date(value);return Number.isNaN(timestamp.getTime())?'—':uiDateTimeFormatter.format(timestamp).replace(',','')}
let offset=0,pageSize=25,hasMore=false,loaded=false,loading=false;
async function loadRuns(nextOffset=offset){
if(loading)return;
loading=true;el('runs-error').textContent='';
for(const id of ['previous-page','next-page','refresh-runs'])el(id).disabled=true;
try{
const response=await fetch('/ingestion/runs?limit=25&offset='+nextOffset+'&sort_by=started_at_utc&sort_dir=desc');
const x=await response.json();if(!response.ok)throw new Error(x?.message||x?.code||response.statusText);
el('runs').replaceChildren();
for(const item of x.items){const tr=document.createElement('tr');[formatDateTime(item.started_at_utc),item.run_type,item.status,item.duration_ms??'—',item.error_message??''].forEach(value=>cell(tr,value,item.status==='failed'?'bad':''));el('runs').append(tr)}
offset=x.page.offset;pageSize=x.page.applied_limit;hasMore=x.page.has_more;loaded=true;
el('runs-summary').textContent=x.page.total===0?'No ingestion runs yet.':`${offset+1}–${offset+x.page.returned} of ${x.page.total} runs · Page ${Math.floor(offset/pageSize)+1} of ${Math.ceil(x.page.total/pageSize)}`;
}catch(error){el('runs-error').textContent=error.message;if(!loaded)el('runs-summary').textContent=''}
finally{loading=false;el('previous-page').disabled=offset===0;el('next-page').disabled=!hasMore;el('refresh-runs').disabled=false}
}
el('previous-page').onclick=()=>loadRuns(Math.max(0,offset-pageSize));
el('next-page').onclick=()=>loadRuns(offset+pageSize);
el('refresh-runs').onclick=()=>loadRuns();
loadRuns();
</script></body></html>"""
