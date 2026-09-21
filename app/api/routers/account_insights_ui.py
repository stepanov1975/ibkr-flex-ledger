"""Account insights dashboard with accessible broker evidence tables."""

ACCOUNT_INSIGHTS_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Account insights</title><style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#151d31;--line:#2a3552;--text:#edf2ff;--muted:#9ba9c7;--accent:#68d5b4}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px system-ui,sans-serif}
main{max-width:1400px;margin:auto;padding:24px}h1{font-size:26px}h2{font-size:19px}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin:18px 0;overflow:auto;max-height:760px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:10px;border-bottom:1px solid var(--line)}
th,.muted{color:var(--muted)}a{color:var(--accent)}button{padding:10px;background:#214f4a;color:var(--text);border:1px solid #327568;border-radius:8px;cursor:pointer}
.bad{color:#ff9ba4}.good{color:var(--accent)}svg{width:100%;height:180px}details{margin:12px 0}td{overflow-wrap:anywhere}
</style></head><body><main><h1>Account insights</h1>
<p class="muted">Statement values, pending income and calculation checks. These are not live balances.</p>
<button id="reload">Refresh</button><p id="status" role="status">Loading...</p><div id="content"></div>
</main><script>
const el=id=>document.getElementById(id);
function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text??'N/A');return n}
function money(value,currency='USD'){if(value===null||value===undefined)return 'N/A';const n=Number(value);if(!Number.isFinite(n))return 'N/A';return currency==='units'?n.toFixed(8).replace(/0+$/,'').replace(/[.]$/,'')+' units':currency+' '+n.toFixed(2)}
function day(value){const m=/^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(String(value??''));return m?m[3]+'/'+m[2]+'/'+m[1].slice(-2):'N/A'}
function table(parent,headers,rows){const t=node('table'),head=node('thead'),tr=node('tr');for(const h of headers)tr.append(node('th',h));head.append(tr);t.append(head);const body=node('tbody');for(const values of rows){const r=node('tr');for(const v of values){const cell=node('td');if(v&&typeof v==='object'&&v.tagName)cell.append(v);else cell.textContent=String(v??'N/A');r.append(cell)}body.append(r)}t.append(body);parent.append(t);if(!rows.length)parent.append(node('p','No records in the selected report.'))}
function instrumentLink(label,id){if(!id)return label;const a=node('a',label);a.href='/ui/stocks/'+encodeURIComponent(id);return a}
function section(title){const s=node('section');s.append(node('h2',title));el('content').append(s);return s}
function check(parent,c,currency='USD'){table(parent,['Check','Broker','Calculated','Difference','Result'],[[c.name,money(c.broker,currency),money(c.calculated,currency),money(c.difference,currency),c.status.replaceAll('_',' ')]]);parent.append(node('p',c.reason))}
function checkText(c,currency){const status=c.status.replaceAll('_',' ');if(c.status==='matched')return status;if(c.status==='not_comparable')return status+': '+c.reason;return status+': broker '+money(c.broker,currency)+'; calculated '+money(c.calculated,currency)+'; difference '+money(c.difference,currency)}
function chart(parent,history){const points=history.filter(p=>p.nav!==null&&Number.isFinite(Number(p.nav)));if(points.length<2)return;const ns='http://www.w3.org/2000/svg';const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 1000 180');svg.setAttribute('role','img');svg.setAttribute('aria-label','Broker account value history. Successive statement observations; exact dates and values are in the following table.');const values=points.map(p=>Number(p.nav));const low=Math.min(...values),span=Math.max(...values)-low||1;const poly=document.createElementNS(ns,'polyline');poly.setAttribute('points',values.map((v,i)=>(20+960*i/(values.length-1))+','+(160-140*(v-low)/span)).join(' '));poly.setAttribute('fill','none');poly.setAttribute('stroke','#68d5b4');poly.setAttribute('stroke-width','3');svg.append(poly);parent.append(svg)}
function renderReport(x){
el('content').replaceChildren();
let s=section('Broker account value');
if(x.nav){const n=x.nav;s.append(node('p','As of '+day(n.date)+' · '+money(n.total,n.currency)));table(s,['Component','Value'],n.components.filter(c=>c.amount===null||Number(c.amount)!==0).map(c=>[c.label,money(c.amount,n.currency)]));table(s,['Cash + positions','Additional NAV components'],[[money(n.cash_and_positions,n.currency),money(n.additional_components,n.currency)]]);check(s,n.check,n.currency)}else s.append(node('p','NAV section unavailable.'));
s=section('Account value history');s.append(node('p','Successive statement observations. See dated values below.'));chart(s,x.nav_history);const details=node('details');details.append(node('summary','Dated values'));table(details,['Date','Broker NAV'],x.nav_history.map(p=>[day(p.date),money(p.nav,p.currency)]));s.append(details);
s=section('What changed during the statement period');
if(x.change_in_nav){const n=x.change_in_nav;s.append(node('p',day(n.from_date)+' to '+day(n.to_date)));table(s,['Starting value','Ending value','Broker time-weighted return'],[[money(n.starting,n.currency),money(n.ending,n.currency),n.twr_percent===null?'N/A':Number(n.twr_percent).toFixed(2)+'%']]);table(s,['Movement','Amount'],n.components.filter(c=>c.amount===null||Number(c.amount)!==0).map(c=>[c.label,money(c.amount,n.currency)]));check(s,n.check,n.currency)}else s.append(node('p','Change in NAV section unavailable.'));
s=section('Commission details');
if(x.commissions){const n=x.commissions;s.append(node('p','Broker signs: negative amounts are charges; positive amounts are rebates. These costs are already in trade commissions and are not added again.'));
s.append(node('p','Statement period '+day(n.from_date)+' to '+day(n.to_date)+' · '+(n.covered_executions??'N/A')+' of '+(n.commissioned_executions??'N/A')+' commissioned executions have uniquely linked details.'));
table(s,['Currency','Signed total'],n.totals.map(r=>[r.currency,money(r.total,r.currency)]));
for(const t of n.totals){table(s,['Component ('+t.currency+')','Signed amount'],t.components.map(r=>[r.label,money(r.amount,t.currency)]));}
table(s,['Date','Instrument','Trade ID','Commission','Component check','Execution check'],n.details.map(r=>[day(r.date),instrumentLink(r.symbol,r.instrument_id),r.trade_id,money(r.total,r.currency),checkText(r.components_check,r.currency),checkText(r.execution_check,r.currency)]));
const d=node('details');d.append(node('summary','Regulatory breakdown (already included in totals)'));table(d,['Trade ID','Regulatory item','Amount'],n.details.flatMap(r=>r.regulatory.map(c=>[r.trade_id,c.label,money(c.amount,r.currency)])));s.append(d);}
s=section('Portfolio concentration');
if(x.concentration){const n=x.concentration;s.append(node('p','Signed market-value weights against broker NAV as of '+day(n.nav_date)+'. Holdings are ordered by absolute market value. Option weights do not measure underlying exposure; currency groups describe quotation currency, not economic currency risk.'));
const pct=v=>v===null?'N/A':Number(v).toFixed(2)+'%';
table(s,['Holding','Asset','Currency','Value (USD)','Broker asset-class weight','Calculated asset-class weight','Account NAV weight','Check'],n.holdings.map(r=>[r.symbol,r.asset_category,r.currency,money(r.value_usd),pct(r.broker_percent),pct(r.class_percent),pct(r.calculated_percent),r.check.status.replaceAll('_',' ')]));
s.append(node('h3','Securities allocation (cash and accruals excluded)'));
table(s,['Asset','Value (USD)','NAV weight'],n.asset_allocation.map(r=>[r.asset_category,money(r.value_usd),pct(r.percent)]));
table(s,['Quotation currency','Securities value (USD)','NAV weight'],n.currency_allocation.map(r=>[r.currency,money(r.value_usd),pct(r.percent)]));}
s=section('Option assignments, exercises and expirations');
s.append(node('p','Linked to existing imported trades. Delivery checks require a unique underlying leg; adjusted contracts may need review.'));
table(s,['Date','Instrument','Activity','Quantity','Multiplier','Gross proceeds','Trade quantity','Cash check','Delivery'],(x.option_activity||[]).map(r=>[day(r.date),instrumentLink(r.symbol,r.instrument_id),r.type,r.quantity,r.multiplier,money(r.proceeds,r.currency),checkText(r.quantity_check,'units'),checkText(r.cash_check,r.currency),r.delivery?checkText(r.delivery,'units'):'Not applicable']));
s=section('Cash settlement');s.append(node('p','Unsettled cash is ending cash minus settled cash. Settled cash is not buying power or an amount available to withdraw.'));
table(s,['Currency','Period','Ending cash','Settled cash','Unsettled cash'],(x.settled_cash||[]).map(r=>[r.currency,day(r.from_date)+' to '+day(r.to_date),money(r.ending,r.currency),money(r.settled,r.currency),money(r.unsettled,r.currency)]));
s=section('Securities lending');
if(x.lending){const n=x.lending;s.append(node('p','Lent shares remain part of your investment holdings. Lending net shares are not your owned position. Collateral is not extra wealth or spending cash.'));
table(s,['Symbol','As of','Owned shares','Borrowed (signed)','Lent shares','Lent %','Net shares','Net check','Holding check'],n.holdings.filter(r=>r.lent===null||r.borrowed===null||Number(r.lent)!==0||Number(r.borrowed)!==0||r.net_check.status==='different'||r.holding_check.status==='different').map(r=>[r.symbol,day(r.date),r.owned,r.borrowed,r.lent,r.lent_percent===null?'N/A':Number(r.lent_percent).toFixed(2)+'%',r.net,checkText(r.net_check,'units'),checkText(r.holding_check,'units')]));
s.append(node('p','NAV lending components as of '+day(n.collateral_date)));table(s,['Component','Amount'],n.collateral.map(r=>[r.label,money(r.amount,n.currency)]));}
s=section('Pending income');
if(x.income){const n=x.income;s.append(node('p','Expected dividends and accrued interest are separate from paid income. Accruals can be corrected by the broker.'));
table(s,['Symbol','As of','Ex date','Pay date','Gross','Tax','Fee','Expected net','Amount check'],n.pending_dividends.map(r=>[r.symbol,day(r.report_date),day(r.ex_date),day(r.pay_date),money(r.gross,r.currency),money(r.tax,r.currency),money(r.fee,r.currency),money(r.net,r.currency),r.amount_check.status]));
table(s,['Currency','Pending dividend total'],n.pending_totals.map(r=>[r.currency,money(r.net,r.currency)]));
table(s,['Interest currency','Period','Starting accrual','Accrued','Reversed','Ending accrual','Check'],n.interest.map(r=>[r.currency+(r.is_base_summary?' (base summary)':''),day(r.from_date)+' to '+day(r.to_date),money(r.starting,r.currency),money(r.accrued,r.currency),money(r.reversal,r.currency),money(r.ending,r.currency),r.check.status]));
const d=node('details');d.append(node('summary','Accrual payment checks'));table(d,['Symbol','Pay date','Expected net','Booked net','Result','Explanation'],n.payment_checks.map(r=>[r.symbol,day(r.pay_date),money(r.net,r.currency),money(r.check.calculated,r.currency),r.check.status.replaceAll('_',' '),r.check.reason]));s.append(d);}
s=section('Calculation checks');s.append(node('p','Matched means within tolerance. Not comparable means evidence is missing, stale or uses a different period.'));
const checks=x.checks||[];s.append(node('p',checks.filter(c=>c.status==='matched').length+' matched · '+checks.filter(c=>c.status==='different').length+' different · '+checks.filter(c=>c.status==='not_comparable').length+' not comparable'));
const filterLabel=node('label'),showMatched=node('input');showMatched.type='checkbox';filterLabel.append(showMatched,node('span',' Show matched checks'));s.append(filterLabel);const checkBody=node('div');s.append(checkBody);
function renderChecks(){checkBody.replaceChildren();table(checkBody,['Instrument','Check','Period','Broker','Calculated','Difference','Result','Explanation'],checks.filter(c=>showMatched.checked||c.status!=='matched').map(c=>[instrumentLink(c.symbol,c.instrument_id),c.name,day(c.from_date)+' to '+day(c.to_date),money(c.broker,c.currency),money(c.calculated,c.currency),money(c.difference,c.currency),c.status.replaceAll('_',' '),c.reason]));}showMatched.onchange=renderChecks;renderChecks();
s=section('Report sources');const sources=node('details');sources.append(node('summary','Statement dates and source record identities'));s.append(sources);table(sources,['Section','Statement date','Artifact','Raw record IDs'],Object.entries(x.sections).map(([name,v])=>[name,day(v.report_date),v.artifact_id,v.raw_ids.join(', ')]));
}
async function load(){el('reload').disabled=true;el('status').textContent='Loading...';try{const r=await fetch('/reports/account-insights');if(!r.ok)throw new Error('Unable to load account insights.');renderReport(await r.json());el('status').textContent='Loaded from successfully imported reports.'}catch(e){el('content').replaceChildren();el('status').textContent=e.message}finally{el('reload').disabled=false}}
el('reload').onclick=load;load();
</script></body></html>"""
