"""Account insights dashboard with accessible broker evidence tables."""

ACCOUNT_INSIGHTS_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Account insights</title><style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#151d31;--line:#2a3552;--text:#edf2ff;--muted:#9ba9c7;--accent:#68d5b4}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px system-ui,sans-serif}
main{max-width:1400px;margin:auto;padding:24px}h1{font-size:26px}h2{font-size:19px}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin:18px 0;overflow:auto}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:10px;border-bottom:1px solid var(--line)}
th,.muted{color:var(--muted)}a{color:var(--accent)}button{padding:10px;background:#214f4a;color:var(--text);border:1px solid #327568;border-radius:8px;cursor:pointer}
.bad{color:#ff9ba4}.good{color:var(--accent)}svg{width:100%;height:180px}details{margin:12px 0}td{overflow-wrap:anywhere}
</style></head><body><main><h1>Account insights</h1>
<p class="muted">Statement values, pending income and calculation checks. These are not live balances.</p>
<button id="reload">Refresh</button><p id="status" role="status">Loading...</p><div id="content"></div>
</main><script>
const el=id=>document.getElementById(id);
function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text??'N/A');return n}
function money(value,currency='USD'){if(value===null||value===undefined)return 'N/A';const n=Number(value);if(!Number.isFinite(n))return 'N/A';return currency+' '+n.toFixed(2)}
function day(value){const m=/^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(String(value??''));return m?m[3]+'/'+m[2]+'/'+m[1].slice(-2):'N/A'}
function table(parent,headers,rows){const t=node('table'),head=node('thead'),tr=node('tr');for(const h of headers)tr.append(node('th',h));head.append(tr);t.append(head);const body=node('tbody');for(const values of rows){const r=node('tr');for(const v of values){const cell=node('td');if(v&&typeof v==='object'&&v.tagName)cell.append(v);else cell.textContent=String(v??'N/A');r.append(cell)}body.append(r)}t.append(body);parent.append(t);if(!rows.length)parent.append(node('p','No records in the selected report.'))}
function section(title){const s=node('section');s.append(node('h2',title));el('content').append(s);return s}
function check(parent,c,currency='USD'){table(parent,['Check','Broker','Calculated','Difference','Result'],[[c.name,money(c.broker,currency),money(c.calculated,currency),money(c.difference,currency),c.status.replaceAll('_',' ')]]);parent.append(node('p',c.reason))}
function chart(parent,history){const points=history.filter(p=>p.nav!==null&&Number.isFinite(Number(p.nav)));if(points.length<2)return;const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 1000 180');svg.setAttribute('role','img');svg.setAttribute('aria-label','Broker account value history. Successive statement observations; exact dates and values are in the following table.');const values=points.map(p=>Number(p.nav));const low=Math.min(...values),span=Math.max(...values)-low||1;const poly=document.createElementNS(ns,'polyline');poly.setAttribute('points',values.map((v,i)=>(20+960*i/(values.length-1))+','+(160-140*(v-low)/span)).join(' '));poly.setAttribute('fill','none');poly.setAttribute('stroke','#68d5b4');poly.setAttribute('stroke-width','3');svg.append(poly);parent.append(svg)}
function renderReport(x){
el('content').replaceChildren();
let s=section('Broker account value');
if(x.nav){const n=x.nav;s.append(node('p','As of '+day(n.date)+' · '+money(n.total,n.currency)));table(s,['Component','Value'],n.components.filter(c=>c.amount===null||Number(c.amount)!==0).map(c=>[c.label,money(c.amount,n.currency)]));table(s,['Cash + positions','Additional NAV components'],[[money(n.cash_and_positions,n.currency),money(n.additional_components,n.currency)]]);check(s,n.check,n.currency)}else s.append(node('p','NAV section unavailable.'));
s=section('Account value history');s.append(node('p','Successive statement observations. See dated values below.'));chart(s,x.nav_history);const details=node('details');details.append(node('summary','Dated values'));table(details,['Date','Broker NAV'],x.nav_history.map(p=>[day(p.date),money(p.nav,p.currency)]));s.append(details);
s=section('What changed during the statement period');
if(x.change_in_nav){const n=x.change_in_nav;s.append(node('p',day(n.from_date)+' to '+day(n.to_date)));table(s,['Starting value','Ending value','Broker time-weighted return'],[[money(n.starting,n.currency),money(n.ending,n.currency),n.twr_percent===null?'N/A':Number(n.twr_percent).toFixed(2)+'%']]);table(s,['Movement','Amount'],n.components.filter(c=>c.amount===null||Number(c.amount)!==0).map(c=>[c.label,money(c.amount,n.currency)]));check(s,n.check,n.currency)}else s.append(node('p','Change in NAV section unavailable.'));
s=section('Calculation checks');s.append(node('p','Matched means within tolerance. Not comparable means evidence is missing, stale or uses a different period.'));
table(s,['Instrument','Check','Period','Broker','Calculated','Difference','Result','Explanation'],(x.checks||[]).map(c=>[c.symbol,c.name,day(c.from_date)+' to '+day(c.to_date),money(c.broker,c.currency),money(c.calculated,c.currency),money(c.difference,c.currency),c.status.replaceAll('_',' '),c.reason]));
s=section('Report sources');table(s,['Section','Statement date','Artifact','Raw record IDs'],Object.entries(x.sections).map(([name,v])=>[name,day(v.report_date),v.artifact_id,v.raw_ids.join(', ')]));
}
async function load(){el('reload').disabled=true;el('status').textContent='Loading...';try{const r=await fetch('/reports/account-insights');if(!r.ok)throw new Error('Unable to load account insights.');renderReport(await r.json());el('status').textContent='Loaded from successfully imported reports.'}catch(e){el('content').replaceChildren();el('status').textContent=e.message}finally{el('reload').disabled=false}}
el('reload').onclick=load;load();
</script></body></html>"""
