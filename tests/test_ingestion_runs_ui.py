"""Exercise ingestion history navigation and the Operations preview."""

import json
import re
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import quickjs

from app.api.routers.ui import api_create_ui_router


def _page_context(
    path: str, count: int = 27, max_limit: int = 200, initial_failure: bool = False,
) -> tuple[quickjs.Context, str, list[dict]]:
    application = FastAPI()
    application.include_router(api_create_ui_router())
    response = TestClient(application).get(path)
    assert response.status_code == 200
    html = response.text
    requests = []
    items = [
        {"ingestion_run_id": f"run-{index}", "started_at_utc": f"2026-08-{28-index:02d}T12:00:00Z",
         "run_type": ("scheduled", "manual", "reprocess")[index % 3],
         "status": "failed" if index == 26 else "success", "duration_ms": index,
         "error_message": "<script>broker error</script>" if index == 26 else None}
        for index in range(count)
    ]

    def api_response(url: str) -> str:
        parsed = urlsplit(url)
        assert parsed.path == "/ingestion/runs"
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        requests.append(query)
        limit = min(int(query.get("limit", 50)), max_limit)
        offset = int(query.get("offset", 0))
        page_items = items[offset:offset + limit]
        return json.dumps({
            "items": page_items,
            "page": {"limit": int(query.get("limit", 50)), "applied_limit": limit,
                     "offset": offset, "returned": len(page_items), "total": count,
                     "has_more": offset + len(page_items) < count},
            "sort": {"sort_by": "started_at_utc", "sort_dir": "desc"},
            "filters": {"status": None, "from_utc": None, "to_utc": None, "run_type": None},
        })

    context = quickjs.Context()
    context.add_callable("apiResponse", api_response)
    context.eval("""
        function node(){return {children:[],textContent:'',className:'',disabled:false,
          append(...items){this.children.push(...items)},replaceChildren(){this.children=[]}}}
        const nodes={};
        const document={getElementById:id=>nodes[id],createElement:()=>node()};
        const Intl={DateTimeFormat:function(){return {format:value=>value.toISOString()}}};
        let fail=false;
        async function fetch(url){return fail
          ? {ok:false,status:503,json:async()=>({message:'History unavailable'})}
          : {ok:true,status:200,json:async()=>JSON.parse(apiResponse(url))}}
    """)
    for identifier in re.findall(r'id="([^"]+)"', html):
        context.eval(f"nodes[{json.dumps(identifier)}]=node()")
    context.eval("fail=" + json.dumps(initial_failure))
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    if path == "/ui/operations":
        script = script.rsplit("loadAll();", 1)[0] + "loadRuns();"
    context.eval(script)
    _flush(context)
    return context, html, requests


def _flush(context: quickjs.Context) -> None:
    while context.execute_pending_job():
        pass


def test_operations_shows_five_latest_runs_and_links_to_full_history() -> None:
    context, html, requests = _page_context("/ui/operations")

    assert context.eval("nodes.runs.children.length") == 5
    assert context.eval("nodes.runs.children[0].children[0].textContent") == "2026-08-28T12:00:00.000Z"
    assert context.eval("nodes.runs.children[4].children[0].textContent") == "2026-08-24T12:00:00.000Z"
    assert 'href="/ui/ingestion-runs"' in html
    assert requests[0]["limit"] == "5"


def test_ingestion_history_paginates_all_run_types_and_preserves_row_details() -> None:
    context, html, requests = _page_context("/ui/ingestion-runs")

    assert "<h1>Ingestion runs</h1>" in html
    assert 'href="/ui/operations"' in html
    assert context.eval("nodes.runs.children.length") == 25
    assert context.eval("nodes['runs-summary'].textContent") == "1–25 of 27 runs · Page 1 of 2"
    assert context.eval("nodes['previous-page'].disabled") is True
    assert context.eval("nodes['next-page'].disabled") is False
    assert json.loads(context.eval("JSON.stringify(nodes.runs.children.slice(0,3).map(row=>row.children[1].textContent))")) == [
        "scheduled", "manual", "reprocess",
    ]
    assert context.eval("nodes.runs.children[0].children[3].textContent") == "0"

    context.eval("nodes['next-page'].onclick()")
    assert context.eval("nodes['next-page'].disabled") is True
    assert context.eval("nodes['previous-page'].disabled") is True
    context.eval("nodes['next-page'].onclick()")
    _flush(context)

    assert context.eval("nodes.runs.children.length") == 2
    assert context.eval("nodes['runs-summary'].textContent") == "26–27 of 27 runs · Page 2 of 2"
    assert context.eval("nodes['next-page'].disabled") is True
    assert context.eval("nodes['previous-page'].disabled") is False
    assert context.eval("nodes.runs.children[1].children[2].textContent") == "failed"
    assert context.eval("nodes.runs.children[1].children[4].textContent") == "<script>broker error</script>"
    assert context.eval("nodes.runs.children[1].children[4].children.length") == 0
    assert context.eval("nodes.runs.children[1].children[2].className") == "bad"

    context.eval("nodes['previous-page'].onclick()")
    _flush(context)
    assert context.eval("nodes.runs.children.length") == 25
    assert [query["offset"] for query in requests] == ["0", "25", "0"]
    assert all(query == {"limit": "25", "offset": offset, "sort_by": "started_at_utc", "sort_dir": "desc"}
               for query, offset in zip(requests, ["0", "25", "0"]))


@pytest.mark.parametrize("count", [0, 25])
def test_ingestion_history_disables_navigation_when_there_is_only_one_page(count: int) -> None:
    context, _, _ = _page_context("/ui/ingestion-runs", count=count)

    assert context.eval("nodes.runs.children.length") == count
    assert context.eval("nodes['previous-page'].disabled") is True
    assert context.eval("nodes['next-page'].disabled") is True
    assert context.eval("nodes['runs-summary'].textContent") == (
        "No ingestion runs yet." if count == 0 else "1–25 of 25 runs · Page 1 of 1"
    )


def test_ingestion_history_uses_server_applied_page_size_without_skipping_runs() -> None:
    context, _, requests = _page_context("/ui/ingestion-runs", max_limit=10)
    context.eval("nodes['next-page'].onclick()")
    _flush(context)

    assert requests[-1]["offset"] == "10"
    assert context.eval("nodes.runs.children[0].children[0].textContent") == "2026-08-18T12:00:00.000Z"
    assert context.eval("nodes['runs-summary'].textContent") == "11–20 of 27 runs · Page 2 of 3"


def test_ingestion_history_failed_navigation_preserves_page_and_allows_retry() -> None:
    context, _, requests = _page_context("/ui/ingestion-runs")
    context.eval("fail=true;nodes['next-page'].onclick()")
    _flush(context)

    assert context.eval("nodes['runs-error'].textContent") == "History unavailable"
    assert context.eval("nodes.runs.children.length") == 25
    assert context.eval("nodes['runs-summary'].textContent") == "1–25 of 27 runs · Page 1 of 2"
    assert context.eval("nodes['next-page'].disabled") is False
    context.eval("fail=false;nodes['next-page'].onclick()")
    _flush(context)
    assert requests[-1]["offset"] == "25"
    assert context.eval("nodes['runs-error'].textContent") == ""



def test_ingestion_history_shows_initial_load_failure() -> None:
    context, _, requests = _page_context("/ui/ingestion-runs", initial_failure=True)

    assert context.eval("nodes['runs-error'].textContent") == "History unavailable"
    assert context.eval("nodes['runs-summary'].textContent") == ""
    assert context.eval("nodes['previous-page'].disabled") is True
    assert context.eval("nodes['next-page'].disabled") is True
