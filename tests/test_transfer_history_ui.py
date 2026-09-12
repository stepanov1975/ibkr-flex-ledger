"""Execute transfer history navigation, formatting, and retry behavior."""

import json
import re
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import quickjs

from app.api.routers.ui import api_create_ui_router


def _flush(context):
    while context.execute_pending_job():
        pass


def _page_context(count=27, max_limit=200, initial_failure=False):
    application = FastAPI()
    application.include_router(api_create_ui_router())
    response = TestClient(application).get("/ui/transfers")
    assert response.status_code == 200
    html = response.text
    requests = []
    items = [
        {"report_date_local": "2026-08-20", "type": "Deposit" if index % 2 == 0 else "Withdrawal",
         "amount": str(index), "currency": "ILS", "description": "<script>transfer</script>" if index else None}
        for index in range(count)
    ]

    def api_response(url):
        parsed = urlsplit(url)
        assert parsed.path == "/reports/transfer-history"
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        requests.append(query)
        limit, offset = min(int(query["limit"]), max_limit), int(query["offset"])
        page_items = items[offset:offset + limit]
        return json.dumps({
            "schema_version": "v1", "items": page_items,
            "page": {"limit": int(query["limit"]), "applied_limit": limit, "offset": offset,
                     "returned": len(page_items), "total": len(items), "has_more": offset + len(page_items) < len(items)},
        })

    context = quickjs.Context()
    context.add_callable("apiResponse", api_response)
    context.eval("""
        function node(){return {children:[],textContent:'',className:'',disabled:false,
          append(...items){this.children.push(...items)},replaceChildren(){this.children=[]}}}
        const nodes={};
        const document={getElementById:id=>nodes[id],createElement:()=>node()};
        const Intl={NumberFormat:function(locale,options){return {format:value=>options.currency+' '+value.toFixed(2)}}};
        let fail=false;
        async function fetch(url){return fail
          ? {ok:false,status:503,json:async()=>({message:'History unavailable'})}
          : {ok:true,status:200,json:async()=>JSON.parse(apiResponse(url))}}
    """)
    for identifier in re.findall(r'id="([^"]+)"', html):
        context.eval(f"nodes[{json.dumps(identifier)}]=node()")
    context.eval("fail=" + json.dumps(initial_failure))
    context.eval(html.split("<script>", 1)[1].split("</script>", 1)[0])
    _flush(context)
    return context, html, requests, items


def test_transfer_history_navigates_pages_and_preserves_formatted_details():
    context, html, requests, _ = _page_context()
    assert "<h1>Transfer history</h1>" in html
    assert 'href="/ui"' in html
    assert context.eval("nodes.transfers.children.length") == 25
    assert context.eval("nodes['transfers-summary'].textContent") == "1–25 of 27 transfers · Page 1 of 2"
    assert context.eval("nodes['previous-page'].disabled") is True
    assert context.eval("nodes['next-page'].disabled") is False
    assert json.loads(context.eval("JSON.stringify(nodes.transfers.children[0].children.map(td=>td.textContent))")) == [
        "20/08/26", "Deposit", "ILS 0.00", "ILS", "",
    ]
    context.eval("nodes['next-page'].onclick()")
    assert context.eval("nodes['previous-page'].disabled && nodes['next-page'].disabled && nodes['refresh-transfers'].disabled")
    context.eval("nodes['next-page'].onclick()")
    _flush(context)
    assert context.eval("nodes.transfers.children.length") == 2
    assert context.eval("nodes['transfers-summary'].textContent") == "26–27 of 27 transfers · Page 2 of 2"
    assert context.eval("nodes['next-page'].disabled") is True
    assert context.eval("nodes['previous-page'].disabled") is False
    assert json.loads(context.eval("JSON.stringify(nodes.transfers.children[0].children.map(td=>td.textContent))")) == [
        "20/08/26", "Withdrawal", "ILS 25.00", "ILS", "<script>transfer</script>",
    ]
    assert context.eval("nodes.transfers.children[0].children[4].children.length") == 0
    context.eval("nodes['previous-page'].onclick()")
    _flush(context)
    assert context.eval("nodes.transfers.children.length") == 25
    assert requests == [{"limit": "25", "offset": offset} for offset in ["0", "25", "0"]]


@pytest.mark.parametrize("count", [0, 25])
def test_transfer_history_handles_empty_and_single_page_results(count):
    context, _, _, _ = _page_context(count=count)
    assert context.eval("nodes.transfers.children.length") == count
    assert context.eval("nodes['previous-page'].disabled && nodes['next-page'].disabled")
    assert context.eval("nodes['transfers-summary'].textContent") == (
        "No transfers yet." if count == 0 else "1–25 of 25 transfers · Page 1 of 1"
    )


def test_transfer_history_uses_the_server_applied_limit():
    context, _, requests, _ = _page_context(max_limit=10)
    context.eval("nodes['next-page'].onclick()")
    _flush(context)
    assert requests[-1]["offset"] == "10"
    assert context.eval("nodes.transfers.children[0].children[2].textContent") == "ILS 10.00"
    assert context.eval("nodes['transfers-summary'].textContent") == "11–20 of 27 transfers · Page 2 of 3"


def test_transfer_history_failed_navigation_preserves_rows_and_allows_retry():
    context, _, requests, _ = _page_context()
    context.eval("fail=true;nodes['next-page'].onclick()")
    _flush(context)
    assert context.eval("nodes['transfers-error'].textContent") == "History unavailable"
    assert context.eval("nodes.transfers.children.length") == 25
    assert context.eval("nodes['transfers-summary'].textContent") == "1–25 of 27 transfers · Page 1 of 2"
    assert context.eval("nodes['next-page'].disabled") is False
    assert context.eval("nodes['refresh-transfers'].disabled") is False
    context.eval("fail=false;nodes['next-page'].onclick()")
    _flush(context)
    assert requests[-1]["offset"] == "25"
    assert context.eval("nodes['transfers-error'].textContent") == ""
    context.eval("nodes['refresh-transfers'].onclick()")
    _flush(context)
    assert requests[-1]["offset"] == "25"


def test_transfer_history_can_retry_an_initial_failure():
    context, _, _, _ = _page_context(initial_failure=True)
    assert context.eval("nodes['transfers-error'].textContent") == "History unavailable"
    assert context.eval("nodes['transfers-summary'].textContent") == ""
    assert context.eval("nodes['previous-page'].disabled && nodes['next-page'].disabled")
    context.eval("fail=false;nodes['refresh-transfers'].onclick()")
    _flush(context)
    assert context.eval("nodes.transfers.children.length") == 25
    assert context.eval("nodes['transfers-error'].textContent") == ""


def test_transfer_history_refresh_recovers_when_the_last_page_disappears():
    context, _, requests, items = _page_context()
    context.eval("nodes['next-page'].onclick()")
    _flush(context)
    del items[2:]
    context.eval("nodes['refresh-transfers'].onclick()")
    _flush(context)
    assert requests[-1]["offset"] == "0"
    assert context.eval("nodes.transfers.children.length") == 2
    assert context.eval("nodes['transfers-summary'].textContent") == "1–2 of 2 transfers · Page 1 of 1"
    assert context.eval("nodes['previous-page'].disabled && nodes['next-page'].disabled")
