"""Corporate-action review must explain uncertainty without clearing it."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from app.api.routers.corporate_actions import api_create_corporate_action_router
from app.db.portfolio import SQLAlchemyPortfolioService
import test_ingestion_integrity_regressions as ingestion_tests
from test_ingestion_integrity_regressions import _harness, _replay
from test_end_to_end_seeded import _SEEDED_PAYLOAD


database = ingestion_tests.database


@pytest.mark.parametrize("action,reason,check", [
    ("CD", "cash payment", "withholding"),
    ("FS", "ratio", "ratio"),
    ("TC", "automatically", "cost basis"),
])
def test_case_explains_why_review_is_needed(database, action, reason, check):
    harness = _harness(database)
    harness[1].payload_bytes = _SEEDED_PAYLOAD.replace(
        b'fifoPnlUnrealized="20"', b'fifoPnlUnrealized="19" costBasisMoney="201" multiplier="1"'
    ).replace(
        b"<CorporateActions />",
        f'<CorporateActions><CorporateAction actionID="REVIEW1" transactionID="REVIEW2" '
        f'conid="900001" symbol="SEED" type="{action}" description="Broker action details" '
        f'currency="USD" reportDate="20260821" /></CorporateActions>'.encode(),
    )
    assert harness[0].job_execute("ingestion_run").status == "success"
    app = FastAPI()
    app.include_router(api_create_corporate_action_router(SQLAlchemyPortfolioService(database)))
    client = TestClient(app)
    case = client.get("/corporate-actions/cases?status=open").json()["items"][0]
    assert case["description"] == "Broker action details"
    assert case["report_date_local"] == "2026-08-21"
    assert reason in case["review_reason"]
    assert check in case["required_check"]
    assert case["requires_manual"] is True


@pytest.mark.parametrize("status", ["resolved", "dismissed"])
def test_acknowledgement_preserves_accounting_uncertainty_and_amounts(database, status):
    harness = _harness(database)
    harness[1].payload_bytes = _SEEDED_PAYLOAD.replace(
        b'fifoPnlUnrealized="20"', b'fifoPnlUnrealized="19" costBasisMoney="201" multiplier="1"'
    ).replace(
        b"<CorporateActions />",
        b'<CorporateActions><CorporateAction actionID="REVIEW1" transactionID="REVIEW2" '
        b'conid="900001" symbol="SEED" type="CD" currency="USD" reportDate="20260821" '
        b'/></CorporateActions>',
    )
    assert harness[0].job_execute("ingestion_run").status == "success"
    repository = SQLAlchemyPortfolioService(database)
    app = FastAPI()
    app.include_router(api_create_corporate_action_router(repository))
    client = TestClient(app)
    case = repository.db_manual_case_list("open")[0]
    with database.connect() as connection:
        before = connection.execute(text(
            "SELECT position_qty, cost_basis, total_pnl FROM pnl_snapshot_daily"
        )).one()
        assert connection.scalar(text("SELECT calculation_provisional FROM pnl_snapshot_daily")) is False
        period = connection.scalar(text("SELECT period_key FROM raw_artifact LIMIT 1"))
    url = f"/corporate-actions/cases/{case.case_id}"
    assert client.patch(url, json={"status": status, "resolution_note": "   "}).status_code == 400
    response = client.patch(url, json={"status": status, "resolution_note": "Checked broker statement"})
    assert response.status_code == 200
    assert response.json()["resolution_note"] == "Checked broker statement"
    assert response.json()["resolved_at_utc"] is not None
    assert client.get("/corporate-actions/cases?status=open").json()["items"] == []
    assert len(client.get(f"/corporate-actions/cases?status={status}").json()["items"]) == 1
    for replay in (False, True):
        if replay:
            assert _replay(harness, period).status == "success"
        with database.connect() as connection:
            assert connection.scalar(text("SELECT provisional FROM event_corp_action")) is True
            assert connection.scalar(text("SELECT provisional FROM pnl_snapshot_daily")) is True
            assert connection.execute(text(
                "SELECT position_qty, cost_basis, total_pnl FROM pnl_snapshot_daily"
            )).one() == before
    assert client.patch(url, json={"status": "open"}).status_code == 200
    assert len(repository.db_manual_case_list("open")) == 1


def test_supported_split_is_processed_without_review(database):
    harness = _harness(database)
    harness[1].payload_bytes = _SEEDED_PAYLOAD.replace(
        b'fifoPnlUnrealized="20"', b'fifoPnlUnrealized="19" costBasisMoney="201" multiplier="1"'
    ).replace(
        b"<CorporateActions />",
        b'<CorporateActions><CorporateAction actionID="AUTO1" transactionID="AUTO2" '
        b'conid="900001" symbol="SEED" type="FS" ratio="2" currency="USD" '
        b'reportDate="20260821" /></CorporateActions>',
    )
    assert harness[0].job_execute("ingestion_run").status == "success"
    assert SQLAlchemyPortfolioService(database).db_manual_case_list("open") == []
    with database.connect() as connection:
        assert connection.scalar(text("SELECT requires_manual FROM event_corp_action")) is False


def test_corrected_broker_split_uses_current_source_and_closes_obsolete_case(database):
    """An explicit correction must reach the ledger, rather than the old raw row."""
    harness = _harness(database)
    payload = _SEEDED_PAYLOAD.replace(
        b"<CorporateActions />",
        b'<CorporateActions><CorporateAction actionID="CORRECT1" transactionID="CORRECT2" '
        b'conid="900001" symbol="SEED" type="FS" currency="USD" reportDate="20260821" '
        b'description="Broker split" /></CorporateActions>',
    )
    harness[1].payload_bytes = payload
    assert harness[0].job_execute("ingestion_run").status == "success"
    assert len(SQLAlchemyPortfolioService(database).db_manual_case_list("open")) == 1
    harness[1].payload_bytes = payload.replace(b'Broker split', b'SEED(US1) SPLIT 3 FOR 2 (SEED, TEST, US1)')
    assert harness[0].job_execute("ingestion_run").status == "success"
    factors = harness[5].db_ledger_corporate_action_list_for_account("INTEGRITY", "2026-08-21")
    assert len(factors) == 1
    assert factors[0].adjustment_factor == "1.5"
    assert SQLAlchemyPortfolioService(database).db_manual_case_list("open") == []
