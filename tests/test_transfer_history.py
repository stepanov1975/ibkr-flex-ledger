"""Transfer history API pagination and canonical PostgreSQL query behavior."""

from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from app.api.routers.reports import api_create_reports_router
from app.config import AppSettings
from app.db import SQLAlchemyPortfolioService, TransferReportRecord
from test_ingestion_integrity_regressions import _harness, database as _database


database = _database


class _Transfers:
    def db_report_transfer_history(self, account_id, limit, offset):
        assert account_id == "U_TEST"
        items = [TransferReportRecord(date(2026, 8, 20), "Withdrawal", "200.50", "ILS", None)] * 27
        return items[offset:offset + limit], len(items)


def _client(repository=None, **settings):
    application = FastAPI()
    application.include_router(api_create_reports_router(
        AppSettings(account_id="U_TEST", **settings), repository or _Transfers(),
    ))
    return TestClient(application)


@pytest.mark.parametrize("offset,returned,has_more", [(0, 25, True), (25, 2, False), (50, 0, False)])
def test_transfer_history_api_returns_page_metadata_and_original_currency(offset, returned, has_more):
    response = _client().get(f"/reports/transfer-history?limit=25&offset={offset}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["page"] == {
        "limit": 25, "applied_limit": 25, "offset": offset, "returned": returned,
        "total": 27, "has_more": has_more,
    }
    assert payload["items"] == [{
        "report_date_local": "2026-08-20", "type": "Withdrawal", "amount": "200.50",
        "currency": "ILS", "description": None,
    }] * returned


def test_transfer_history_api_applies_configured_pagination_limits():
    client = _client(api_default_limit=5, api_max_limit=10)
    assert client.get("/reports/transfer-history").json()["page"]["returned"] == 5
    payload = client.get("/reports/transfer-history?limit=25&offset=10").json()
    assert payload["page"] == {
        "limit": 25, "applied_limit": 10, "offset": 10, "returned": 10, "total": 27, "has_more": True,
    }


@pytest.mark.parametrize("query", ["limit=0", "limit=-1", "offset=-1"])
def test_transfer_history_api_rejects_invalid_pagination(query):
    response = _client().get("/reports/transfer-history?" + query)
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PAGINATION"


def test_transfer_history_pages_canonical_transfers_with_stable_order_and_account_scope(database):
    orchestrator, adapter, *_ = _harness(database, account="U_TEST")
    transfers = ''.join(
        f'<CashTransaction transactionID="{10000 + index}" type="Deposits/Withdrawals" '
        f'amount="{index if index % 2 == 0 else -index}" currency="ILS" '
        f'reportDate="202608{19 if index < 10 else 20}" description="Transfer {index}" />'
        for index in range(27)
    )
    adapter.payload_bytes = adapter.payload_bytes.replace(b'</CashTransactions>', transfers.encode() + b'</CashTransactions>')
    assert orchestrator.job_execute("ingestion_run").status == "success"
    # Fixed IDs make same-day tie ordering independently verifiable across page boundaries.
    with database.begin() as connection:
        connection.execute(text(
            "UPDATE event_cashflow SET event_cashflow_id=CAST(lpad(to_hex(transaction_id::int), 32, '0') AS uuid)"
        ))
    repository = SQLAlchemyPortfolioService(database)
    client = _client(repository)
    first = client.get("/reports/transfer-history?limit=25").json()
    second = client.get("/reports/transfer-history?limit=25&offset=25").json()
    rows = first["items"] + second["items"]
    assert first["page"]["total"] == second["page"]["total"] == 28
    assert first["page"]["has_more"] is True
    assert second["page"]["has_more"] is False
    assert [row["description"] for row in rows] == (
        [f"Transfer {index}" for index in range(26, 9, -1)] + ["Seed deposit"]
        + [f"Transfer {index}" for index in range(9, -1, -1)]
    )
    assert rows[1] == {
        "report_date_local": "2026-08-20", "type": "Withdrawal", "amount": "25.00000000",
        "currency": "ILS", "description": "Transfer 25",
    }
    assert rows[-1]["type"] == "Deposit"
    assert repository.db_report_transfer_history("OTHER", 25, 0) == ([], 0)
    assert repository.db_report_transfer_history("U_TEST", 25, 100) == ([], 28)
