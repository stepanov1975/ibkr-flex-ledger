"""Execute the portfolio links and stock-history renderer with a minimal DOM."""

import json
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import quickjs

from app.api.routers.ui import api_create_ui_router


def _page(path):
    application = FastAPI()
    application.include_router(api_create_ui_router())
    return TestClient(application).get(path)


def _context(script):
    context = quickjs.Context()
    context.eval('''
        function makeNode(tag=''){return {tag,children:[],_text:'',className:'',checked:true,
          get textContent(){return this._text+this.children.map(child=>child.textContent).join('')},
          set textContent(value){this._text=String(value);this.children=[]},
          append(...items){this.children.push(...items)},replaceChildren(){this.children=[];this._text=''}}}
        const nodes={};const document={title:'',getElementById:id=>nodes[id]||(nodes[id]=makeNode()),
          createElement:tag=>makeNode(tag)};
        const Intl={NumberFormat:function(){return {format:value=>Number(value).toFixed(2)}},
          DateTimeFormat:function(){return {format:value=>'21/08/26 13:00'}}};
    ''')
    context.eval(script)
    return context


def test_portfolio_symbols_are_links_to_instrument_history():
    response = _page('/ui')
    script = response.text.split('<script>')[1].split('</script>')[0].rsplit('loadAll();', 1)[0]
    context = _context(script)
    identifier = str(uuid4())
    context.eval('latestPnlItems='+json.dumps([
        {'instrument_id': identifier, 'symbol': '<TEST>', 'position_qty': '3', 'currency': 'USD'},
    ])+';renderPnl();')
    assert context.eval("nodes.pnl.children[0].children[0].children[0].tag") == 'a'
    assert context.eval("nodes.pnl.children[0].children[0].children[0].href") == f'/ui/stocks/{identifier}'
    assert context.eval("nodes.pnl.children[0].children[0].textContent") == '<TEST>'


def test_history_renders_pnl_closed_and_open_operations_and_errors():
    identifier = str(uuid4())
    response = _page(f'/ui/stocks/{identifier}')
    assert response.status_code == 200
    script = response.text.split('<script>')[1].split('</script>')[0].rsplit('loadHistory();', 1)[0]
    context = _context(script)
    context.eval('''
      let requestedUrl='';
      fetch=async url=>{requestedUrl=url;return {ok:true,json:async()=>({
        symbol:'<TEST>',report_date_local:'2026-08-21',provisional:false,
        totals:[{currency:'USD',realized_pnl:'481.2',unrealized_pnl:'427.8',total_pnl:'909'}],
        positions:[],lots:[
          {symbol:'TEST',status:'partially_closed',opened_at_utc:'2026-08-20T10:00:00Z',
           open_quantity:'10',remaining_quantity:'6',realized_pnl:'78.2',unrealized_pnl:null,currency:'USD'},
          {symbol:'TEST option',status:'closed',opened_at_utc:'2026-08-20T10:00:00Z',
           closed_at_utc:'2026-08-21T10:00:00Z',open_quantity:'1',remaining_quantity:'0',
           realized_pnl:'200',unrealized_pnl:'0',currency:'USD'}],
        activity:[{report_date_local:'2026-08-21',symbol:'TEST',event_type:'cashflow',action:'Dividends',
          amount:'5',currency:'USD',description:'<safe text>'}]
      })}};
      loadHistory();
    ''')
    while context.execute_pending_job():
        pass
    assert context.eval('requestedUrl') == f'/reports/stock-history/{identifier}'
    assert context.eval("nodes['stock-name'].textContent") == '<TEST>'
    assert context.eval("nodes['realized-pnl'].textContent") == '481.20'
    assert context.eval("nodes['unrealized-pnl'].textContent") == '427.80'
    rows = json.loads(context.eval("JSON.stringify(nodes.lots.children.map(row=>row.children.map(cell=>cell.textContent)))"))
    assert 'Partially closed' in rows[0]
    assert rows[0][-2:] == ['78.20', 'N/A']
    assert 'Closed' in rows[1]
    assert rows[1][-2:] == ['200.00', '0.00']
    assert '<safe text>' in context.eval('nodes.activity.textContent')
    context.eval("fetch=async()=>({ok:false,statusText:'Not Found',json:async()=>({message:'Instrument not found'})});loadHistory();")
    while context.execute_pending_job():
        pass
    assert context.eval("nodes['history-error'].textContent") == 'Instrument not found'
    assert context.eval("nodes['realized-pnl'].textContent") == 'N/A'
    assert context.eval('nodes.lots.children.length') == 0


def test_history_explains_when_pnl_snapshots_lag_behind_activity():
    response = _page(f'/ui/stocks/{uuid4()}')
    script = response.text.split('<script>')[1].split('</script>')[0].rsplit('loadHistory();', 1)[0]
    context = _context(script)
    context.eval('''renderHistory({symbol:'TEST',report_date_local:'2026-08-21',provisional:true,stale:true,
      totals:[],positions:[],lots:[],activity:[]})''')
    message = context.eval("nodes['history-state'].textContent").lower()
    assert 'snapshot' in message
    assert 'activity' in message


def test_overlapping_refreshes_do_not_duplicate_rows_and_can_refresh_after_completion():
    response = _page(f'/ui/stocks/{uuid4()}')
    script = response.text.split('<script>')[1].split('</script>')[0].rsplit('loadHistory();', 1)[0]
    context = _context(script)
    context.eval('''
      const pending=[];
      fetch=()=>new Promise(resolve=>pending.push(resolve));
      const report={symbol:'TEST',totals:[],positions:[{symbol:'TEST'}],
        lots:[{symbol:'TEST',status:'open'}],activity:[{symbol:'TEST',event_type:'trade'}]};
      loadHistory();loadHistory();
      for(const resolve of pending)resolve({ok:true,json:async()=>report});
    ''')
    while context.execute_pending_job():
        pass
    for table in ('positions', 'lots', 'activity'):
        assert context.eval(f'nodes.{table}.children.length') == 1
    context.eval('''
      loadHistory();
      pending[pending.length-1]({ok:true,json:async()=>({...report,symbol:'UPDATED',positions:[],lots:[],activity:[]})});
    ''')
    while context.execute_pending_job():
        pass
    assert context.eval("nodes['stock-name'].textContent") == 'UPDATED'
    assert context.eval('nodes.activity.children.length') == 0
