"""Atomic split preview/application using real PostgreSQL and FIFO accounting."""

from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from app.api.routers.corporate_actions import api_create_corporate_action_router
from app.db.portfolio import SQLAlchemyPortfolioService
import test_ingestion_integrity_regressions as ingestion_tests
from test_end_to_end_seeded import _SEEDED_PAYLOAD


database = ingestion_tests.database


@pytest.fixture
def split_case(database, request):
    harness = ingestion_tests._harness(database)
    harness[1].payload_bytes = _SEEDED_PAYLOAD.replace(
        b'reportDate="20260821" dateTime="20260821;120000"',
        b'reportDate="20260820" dateTime="20260820;120000"',
    ).replace(b'position="2"', b'position="3"').replace(
        b'fifoPnlUnrealized="20"', b'fifoPnlUnrealized="129" costBasisMoney="201" multiplier="1"',
    ).replace(
        b"<CorporateActions />",
        b'<CorporateActions><CorporateAction actionID="SPLIT1" transactionID="SPLIT2" '
        b'conid="900001" symbol="SEED" type="FS" currency="USD" reportDate="20260821" '
        b'description="Broker split with missing ratio" /></CorporateActions>',
    )
    if getattr(request, "param", None) in {"reverse", "reverse_multiple", "reverse_preclose"}:
        harness[1].payload_bytes = harness[1].payload_bytes.replace(b'type="FS"', b'type="RS"').replace(
            b'buySell="BUY" quantity="2"', b'buySell="BUY" quantity="3"',
        ).replace(b'position="3"', b'position="1"').replace(b'costBasisMoney="201"', b'costBasisMoney="301"')
    if getattr(request, "param", None) in {"reverse_multiple", "reverse_preclose"}:
        harness[1].payload_bytes = harness[1].payload_bytes.replace(
            b'buySell="BUY" quantity="3"', b'buySell="BUY" quantity="1"',
        ).replace(
            b'</Trades>',
            b'<Trade transactionID="BUY2" ibExecID="BUY2" conid="900001" symbol="SEED" '
            b'assetCategory="STK" buySell="BUY" quantity="1" tradePrice="100" currency="USD" '
            b'reportDate="20260820" dateTime="20260820;120100" ibCommission="0" fxRateToBase="1" />'
            b'<Trade transactionID="BUY3" ibExecID="BUY3" conid="900001" symbol="SEED" '
            b'assetCategory="STK" buySell="BUY" quantity="1" tradePrice="100" currency="USD" '
            b'reportDate="20260820" dateTime="20260820;120200" ibCommission="0" fxRateToBase="1" /></Trades>',
        )
    if getattr(request, "param", None) == "reverse_preclose":
        harness[1].payload_bytes = harness[1].payload_bytes.replace(
            b'quantity="1" tradePrice="100" currency="USD" '
            b'reportDate="20260820" dateTime="20260820;120100"',
            b'quantity="1" tradePrice="300" currency="USD" '
            b'reportDate="20260820" dateTime="20260820;120100"',
        ).replace(
            b'buySell="BUY" quantity="1" tradePrice="100" currency="USD" '
            b'reportDate="20260820" dateTime="20260820;120200"',
            b'buySell="SELL" quantity="-1" tradePrice="200" currency="USD" '
            b'reportDate="20260820" dateTime="20260820;120200"',
        )
    assert harness[0].job_execute("ingestion_run").status == "success"
    case = SQLAlchemyPortfolioService(database).db_manual_case_list("open")[0]
    from app.db.corporate_action_correction import SQLAlchemySplitCorrectionService
    application = FastAPI()
    application.include_router(api_create_corporate_action_router(
        SQLAlchemyPortfolioService(database),
        correction_service=SQLAlchemySplitCorrectionService(database, "INTEGRITY"),
    ))
    return harness, case, TestClient(application)


def _state(database):
    with database.connect() as connection:
        return {
            table: connection.execute(text(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY 1")).scalars().all()
            for table in ("raw_record", "event_corp_action", "corporate_action_manual_case", "pnl_snapshot_daily", "position_lot")
        }


_RATIO = {"new_shares": "3", "old_shares": "2", "note": "Checked broker split notice"}


def test_preview_rolls_back_apply_rebuilds_and_replay_preserves_correction(database, split_case):
    harness, case, client = split_case
    base = f"/corporate-actions/cases/{case.case_id}/split"
    before = _state(database)
    preview = client.post(base + "/preview", json=_RATIO)
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert _state(database) == before
    assert result["factor"] == "1.5"
    assert len(result["snapshots"]) == 1
    assert result["snapshots"][0]["before"]["provisional"] is True
    assert result["snapshots"][0]["after"]["provisional"] is False
    assert Decimal(result["lots_before"][0]["remaining_quantity"]) == 2
    assert Decimal(result["lots_after"][0]["remaining_quantity"]) == 3
    applied = client.post(base + "/apply", json={**_RATIO, "preview_token": result["preview_token"]})
    assert applied.status_code == 200, applied.text
    state = _state(database)
    assert state["raw_record"] == before["raw_record"]
    with database.connect() as c:
        assert c.scalar(text("SELECT requires_manual FROM event_corp_action")) is False
        assert c.scalar(text("SELECT provisional FROM pnl_snapshot_daily")) is False
        assert c.scalar(text("SELECT split_factor FROM corporate_action_manual_case")) == Decimal("1.5")
        assert c.scalar(text("SELECT remaining_quantity FROM position_lot WHERE status='open'")) == 3
        period = c.scalar(text("SELECT period_key FROM raw_artifact LIMIT 1"))
    assert client.post(base + "/apply", json={**_RATIO, "preview_token": result["preview_token"]}).status_code == 409
    assert ingestion_tests._replay(harness, period).status == "success"
    with database.connect() as c:
        assert c.scalar(text("SELECT requires_manual FROM event_corp_action")) is False
        assert c.scalar(text("SELECT provisional FROM pnl_snapshot_daily")) is False
        assert c.scalar(text("SELECT remaining_quantity FROM position_lot WHERE status='open'")) == 3
    # A changed source invalidates the manual override and reopens the case.
    harness[1].payload_bytes = harness[1].payload_bytes.replace(b'missing ratio', b'updated, missing ratio')
    assert harness[0].job_execute("ingestion_run").status == "success"
    with database.connect() as c:
        assert c.scalar(text("SELECT requires_manual FROM event_corp_action")) is True
        assert c.scalar(text("SELECT status FROM corporate_action_manual_case")) == "open"
        assert c.scalar(text("SELECT provisional FROM pnl_snapshot_daily")) is True


@pytest.mark.parametrize("body", [
    {**_RATIO, "new_shares": "0"}, {**_RATIO, "old_shares": "-2"},
    {**_RATIO, "new_shares": "NaN"}, {**_RATIO, "old_shares": "Infinity"},
    {**_RATIO, "note": "   "},
    {**_RATIO, "new_shares": "999999999999999999", "old_shares": "0.00000001"},
])
def test_invalid_correction_never_mutates_data(database, split_case, body):
    _, case, client = split_case
    before = _state(database)
    response = client.post(f"/corporate-actions/cases/{case.case_id}/split/preview", json=body)
    assert response.status_code in (400, 422)
    assert _state(database) == before


def test_stale_preview_and_active_ingestion_are_rejected(database, split_case):
    harness, case, client = split_case
    base = f"/corporate-actions/cases/{case.case_id}/split"
    preview = client.post(base + "/preview", json=_RATIO).json()
    with database.begin() as c:
        c.execute(text("UPDATE event_trade_fill SET price=101"))
    before = _state(database)
    result = client.post(base + "/apply", json={**_RATIO, "preview_token": preview["preview_token"]})
    assert result.status_code == 409
    assert _state(database) == before
    run = harness[6].db_ingestion_run_create_started("INTEGRITY", "manual", "2026-08-21", "test", None)
    assert client.post(base + "/preview", json=_RATIO).status_code == 409
    harness[6].db_ingestion_run_finalize(run.ingestion_run_id, "failed", "TEST", "Test complete", [])


def test_unsupported_and_missing_identity_cannot_be_applied(database, split_case):
    _, case, client = split_case
    base = f"/corporate-actions/cases/{case.case_id}/split/preview"
    for update in ("reorg_code='SPINOFF'", "reorg_code='FORWARDSPLIT', action_id=NULL"):
        with database.begin() as c:
            c.execute(text("UPDATE event_corp_action SET " + update))
        before = _state(database)
        assert client.post(base, json=_RATIO).status_code == 400
        assert _state(database) == before


def test_failed_rebuild_rolls_back_correction_and_all_snapshots(database, split_case, monkeypatch):
    from app.ledger import StockLedgerSnapshotService
    _, case, client = split_case
    base = f"/corporate-actions/cases/{case.case_id}/split"
    preview = client.post(base + "/preview", json=_RATIO).json()
    before = _state(database)
    rebuild = StockLedgerSnapshotService.ledger_snapshot_build_and_persist

    def fail_after_writes(self, **kwargs):
        rebuild(self, **kwargs)
        raise RuntimeError("Simulated rebuild failure")

    monkeypatch.setattr(StockLedgerSnapshotService, "ledger_snapshot_build_and_persist", fail_after_writes)
    response = client.post(base + "/apply", json={**_RATIO, "preview_token": preview["preview_token"]})
    assert response.status_code == 500
    assert _state(database) == before


def test_apply_rebuilds_later_realized_pnl_and_rolls_back_all_dates_on_failure(database, split_case, monkeypatch):
    """A sale after the split must use the adjusted per-share FIFO cost."""
    from app.ledger import StockLedgerSnapshotService
    harness, case, client = split_case
    harness[1].payload_bytes = harness[1].payload_bytes.replace(
        b'<FlexStatement reportDate="20260821">', b'<FlexStatement reportDate="20260822">',
    ).replace(
        b'reportDate="20260821" position="3"', b'reportDate="20260822" position="2"',
    ).replace(b'costBasisMoney="201"', b'costBasisMoney="134"').replace(
        b'fifoPnlUnrealized="129"', b'fifoPnlUnrealized="86"',
    ).replace(
        b'</Trades>',
        b'<Trade transactionID="SELL1" ibExecID="SELL1" conid="900001" symbol="SEED" '
        b'assetCategory="STK" buySell="SELL" quantity="-1" tradePrice="120" closePrice="110" '
        b'currency="USD" reportDate="20260822" dateTime="20260822;120000" '
        b'ibCommission="0" fees="0" fxRateToBase="1" /></Trades>',
    )
    assert harness[0].job_execute("ingestion_run").status == "success"
    base = f"/corporate-actions/cases/{case.case_id}/split"
    before = _state(database)
    preview = client.post(base + "/preview", json=_RATIO)
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert len(result["snapshots"]) == 2
    assert Decimal(result["snapshots"][1]["before"]["realized_pnl"]) == Decimal("19.5")
    assert Decimal(result["snapshots"][1]["after"]["realized_pnl"]) == Decimal("53")
    assert _state(database) == before
    rebuild = StockLedgerSnapshotService.ledger_snapshot_build_and_persist
    calls = []

    def fail_second_date(self, **kwargs):
        rebuild(self, **kwargs)
        calls.append(kwargs["report_date_local"])
        if len(calls) == 2:
            raise RuntimeError("Second date failed")

    monkeypatch.setattr(StockLedgerSnapshotService, "ledger_snapshot_build_and_persist", fail_second_date)
    assert client.post(base + "/apply", json={**_RATIO, "preview_token": result["preview_token"]}).status_code == 500
    assert len(calls) == 2
    assert _state(database) == before
    monkeypatch.setattr(StockLedgerSnapshotService, "ledger_snapshot_build_and_persist", rebuild)
    assert client.post(base + "/apply", json={**_RATIO, "preview_token": result["preview_token"]}).status_code == 200
    with database.connect() as c:
        assert c.execute(text("SELECT realized_pnl FROM pnl_snapshot_daily ORDER BY report_date_local")).scalars().all() == [0, 53]
        assert c.scalar(text("SELECT remaining_quantity FROM position_lot WHERE status='open'")) == 2


def test_correction_is_account_scoped_and_requires_an_affected_snapshot(database, split_case):
    from app.db.corporate_action_correction import SQLAlchemySplitCorrectionService
    _, case, client = split_case
    before = _state(database)
    with pytest.raises(LookupError):
        SQLAlchemySplitCorrectionService(database, "OTHER").preview_or_apply(case.case_id, Decimal(3), Decimal(2), "Test")
    assert _state(database) == before
    with database.begin() as c:
        c.execute(text("DELETE FROM pnl_snapshot_daily"))
    before = _state(database)
    assert client.post(f"/corporate-actions/cases/{case.case_id}/split/preview", json=_RATIO).status_code == 409
    assert _state(database) == before


def test_pre_action_snapshot_only_loses_the_resolved_manual_flag(database, split_case):
    _, case, client = split_case
    with database.begin() as c:
        c.execute(text(
            "INSERT INTO pnl_snapshot_daily (account_id, report_date_local, instrument_id, position_qty, cost_basis, "
            "realized_pnl, unrealized_pnl, total_pnl, fees, withholding_tax, currency, calculation_provisional, "
            "provisional, valuation_source, fx_source, ingestion_run_id) "
            "SELECT account_id, '2026-08-20', instrument_id, 2, 201, 0, 19, 19, fees, withholding_tax, currency, "
            "false, true, valuation_source, fx_source, ingestion_run_id FROM pnl_snapshot_daily"
        ))
    base = f"/corporate-actions/cases/{case.case_id}/split"
    before = _state(database)
    result = client.post(base + "/preview", json=_RATIO).json()
    assert len(result["snapshots"]) == 2
    earlier = result["snapshots"][0]
    assert earlier["before"]["provisional"] is True
    assert earlier["after"]["provisional"] is False
    for key in ("position_qty", "cost_basis", "realized_pnl", "unrealized_pnl", "total_pnl"):
        assert earlier["after"][key] == earlier["before"][key]
    assert _state(database) == before
    assert client.post(base + "/apply", json={**_RATIO, "preview_token": result["preview_token"]}).status_code == 200


@pytest.mark.parametrize("split_case", ["reverse", "reverse_multiple"], indirect=True)
def test_reverse_thirds_do_not_create_fractional_dust_or_false_mismatch(database, split_case):
    harness, case, client = split_case
    base = f"/corporate-actions/cases/{case.case_id}/split"
    ratio = {**_RATIO, "new_shares": "1", "old_shares": "3"}
    result = client.post(base + "/preview", json=ratio).json()
    assert result["snapshots"][0]["after"]["provisional"] is False, result
    assert sum(Decimal(lot["remaining_quantity"]) for lot in result["lots_after"]) == 1
    assert sum(Decimal(lot["cost_basis_open"]) for lot in result["lots_after"]) == 301
    assert client.post(base + "/apply", json={**ratio, "preview_token": result["preview_token"]}).status_code == 200
    harness[1].payload_bytes = harness[1].payload_bytes.replace(
        b'<FlexStatement reportDate="20260821">', b'<FlexStatement reportDate="20260822">',
    ).replace(b'reportDate="20260821" position="1"', b'reportDate="20260822" position="0"').replace(
        b'costBasisMoney="301"', b'costBasisMoney="0"',
    ).replace(b'fifoPnlUnrealized="129"', b'fifoPnlUnrealized="0"').replace(
        b'</Trades>', b'<Trade transactionID="SELL1" ibExecID="SELL1" conid="900001" symbol="SEED" '
        b'assetCategory="STK" buySell="SELL" quantity="-1" tradePrice="320" closePrice="110" '
        b'currency="USD" reportDate="20260822" dateTime="20260822;120000" ibCommission="0" fxRateToBase="1" /></Trades>',
    )
    assert harness[0].job_execute("ingestion_run").status == "success"
    with database.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM position_lot WHERE status='open'")) == 0
        last = c.execute(text("SELECT position_qty, realized_pnl, provisional FROM pnl_snapshot_daily ORDER BY report_date_local DESC LIMIT 1")).one()
        assert last == (0, 19, False)


def test_inconsistent_source_identity_requires_accounting_support(database, split_case):
    _, case, client = split_case
    with database.begin() as c:
        c.execute(text("UPDATE event_corp_action SET conid='OTHER'"))
    before = _state(database)
    response = client.post(f"/corporate-actions/cases/{case.case_id}/split/preview", json=_RATIO)
    assert response.status_code == 400
    listed = client.get("/corporate-actions/cases").json()["items"][0]
    assert listed["can_correct_split"] is False
    assert _state(database) == before


@pytest.mark.parametrize("split_case", ["reverse_preclose"], indirect=True)
def test_split_preserves_completed_pre_action_fifo_sale(database, split_case):
    harness, case, client = split_case
    ratio = {**_RATIO, "new_shares": "1", "old_shares": "3"}
    response = client.post(f"/corporate-actions/cases/{case.case_id}/split/preview", json=ratio)
    assert response.status_code == 200, response.text
    snapshot = response.json()["snapshots"][0]
    assert Decimal(snapshot["before"]["realized_pnl"]) == 99
    assert Decimal(snapshot["after"]["realized_pnl"]) == 99
    assert Decimal(response.json()["lots_after"][0]["cost_basis_open"]) == 300


def test_correction_rejects_canonical_activity_beyond_snapshot_horizon(database, split_case, monkeypatch):
    harness, case, client = split_case
    base = f"/corporate-actions/cases/{case.case_id}/split"
    preview = client.post(base + "/preview", json=_RATIO).json()
    harness[1].payload_bytes = harness[1].payload_bytes.replace(
        b'<FlexStatement reportDate="20260821">', b'<FlexStatement reportDate="20260822">',
    ).replace(
        b'</Trades>', b'<Trade transactionID="NEWBUY" ibExecID="NEWBUY" conid="900001" symbol="SEED" '
        b'assetCategory="STK" buySell="BUY" quantity="1" tradePrice="120" currency="USD" '
        b'reportDate="20260822" dateTime="20260822;120000" ibCommission="0" fxRateToBase="1" /></Trades>',
    )

    def fail_snapshot(requests):
        raise RuntimeError("Failure after canonical trades and current lots are committed")

    monkeypatch.setattr(harness[5], "db_pnl_snapshot_daily_upsert_many", fail_snapshot)
    assert harness[0].job_execute("ingestion_run").status == "failed"
    with database.connect() as c:
        assert c.scalar(text("SELECT sum(remaining_quantity) FROM position_lot WHERE status='open'")) == 3
    before = _state(database)
    for endpoint, body in (("preview", _RATIO), ("apply", {**_RATIO, "preview_token": preview["preview_token"]})):
        response = client.post(base + "/" + endpoint, json=body)
        assert response.status_code == 409, response.text
        assert "newer" in response.text.lower()
        assert _state(database) == before
