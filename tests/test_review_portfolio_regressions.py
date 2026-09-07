"""PostgreSQL regressions for review findings in portfolio workflows."""

import os
import json
from datetime import date
from decimal import Decimal
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text

from app.api.routers.master_data import api_create_master_data_router
from app.config import AppSettings
from app.db.portfolio import SQLAlchemyPortfolioService
from test_end_to_end_seeded import _create_database, _database_url, _drop_database, _reachable_database_url


@pytest.fixture
def portfolio_db():
    base = _reachable_database_url()
    name = "review_portfolio_" + uuid4().hex[:12]
    _create_database(base, name)
    url = _database_url(base, name)
    previous = os.environ.get("DATABASE_URL")
    engine = create_engine(url)
    try:
        os.environ["DATABASE_URL"] = url
        command.upgrade(Config("alembic.ini"), "head")
        yield engine, SQLAlchemyPortfolioService(engine)
    finally:
        engine.dispose()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        _drop_database(base, name)


def test_label_notes_deletion_and_explicit_color_clear(portfolio_db) -> None:
    engine, repository = portfolio_db
    app = FastAPI()
    settings = AppSettings(ibkr_flex_token="test", ibkr_flex_query_id="test", account_id="REVIEW")
    app.include_router(api_create_master_data_router(settings, repository))
    client = TestClient(app, raise_server_exceptions=False)
    label = client.post("/labels", json={"name": "Review", "color": "#ffffff"}).json()
    target = "/labels/" + label["label_id"]
    assert client.patch(target, json={"name": "Renamed"}).json()["color"] == "#ffffff"
    assert client.patch(target, json={"color": None}).json()["color"] is None
    note = client.post("/notes", json={"label_id": label["label_id"], "content": "Keep me"}).json()
    response = client.delete(target)
    assert response.status_code == 409
    assert response.json()["code"] == "LABEL_IN_USE"
    assert client.get("/notes").json()["items"][0]["content"] == "Keep me"
    assert client.delete("/notes/" + note["note_id"]).status_code == 204
    with engine.begin() as connection:
        instrument = connection.execute(text(
            "INSERT INTO instrument(account_id, conid, symbol, asset_category, currency) "
            "VALUES ('REVIEW', '1', 'TEST', 'STK', 'USD') RETURNING instrument_id"
        )).scalar_one()
    note = client.post("/notes", json={
        "label_id": label["label_id"], "instrument_id": str(instrument), "content": "Dual target",
    }).json()
    assert client.delete(target).status_code == 204
    remaining = client.get("/notes").json()["items"][0]
    assert remaining["note_id"] == note["note_id"]
    assert remaining["label_id"] is None
    assert remaining["instrument_id"] == str(instrument)


@pytest.mark.parametrize("trade_currency", ["USD", "EUR"])
def test_reconciliation_uses_cumulative_unique_events_and_positive_fees(portfolio_db, trade_currency) -> None:
    engine, repository = portfolio_db
    expected_realized = Decimal("30") * (Decimal("1.2") if trade_currency == "EUR" else 1)
    with engine.begin() as connection:
        instrument = connection.execute(text(
            "INSERT INTO instrument(account_id, conid, symbol, asset_category, currency) "
            "VALUES ('REVIEW', '1', 'TEST', 'STK', 'USD') RETURNING instrument_id"
        )).scalar_one()
        for day, pnl in ((20, 10), (21, 20), (22, 999)):
            run = connection.execute(text(
                "INSERT INTO ingestion_run(account_id, run_type, status, period_key, flex_query_id, started_at_utc) "
                "VALUES ('REVIEW', 'manual', 'success', :day, 'query', now()) RETURNING ingestion_run_id"
            ), {"day": f"2026-08-{day}"}).scalar_one()
            params = {"run": run, "day": f"2026-08-{day}", "instrument": instrument, "pnl": pnl,
                      "currency": trade_currency, "expected": expected_realized}
            payload = {"conid": "1", "currency": trade_currency, "ibCommission": "-1",
                       "ibCommissionCurrency": "USD", "fifoPnlRealized": str(pnl)}
            raw = connection.execute(text(
                "INSERT INTO raw_record(ingestion_run_id, account_id, period_key, flex_query_id, "
                "payload_sha256, report_date_local, section_name, source_row_ref, source_payload) "
                "VALUES (:run, 'REVIEW', :day, 'query', :day, CAST(CAST(:day AS text) AS date), 'Trades', :day, "
                "CAST(:payload AS jsonb)) RETURNING raw_record_id"
            ), {**params, "payload": json.dumps(payload)}).scalar_one()
            connection.execute(text(
                "INSERT INTO event_trade_fill(account_id, instrument_id, ingestion_run_id, source_raw_record_id, "
                "ib_exec_id, trade_timestamp_utc, report_date_local, side, quantity, price, currency, commission, realized_pnl, fx_rate_to_base) "
                "VALUES ('REVIEW', :instrument, :run, :raw, :day, CAST(CAST(:day AS text) AS timestamptz), CAST(CAST(:day AS text) AS date), "
                "'SELL', 1, 100, :currency, -1, :pnl, 0)"
            ), {**params, "raw": raw})
            if day == 21:
                # An overlapping raw report is retained but is not a second economic event.
                connection.execute(text(
                    "INSERT INTO raw_record(ingestion_run_id, account_id, period_key, flex_query_id, "
                    "payload_sha256, report_date_local, section_name, source_row_ref, source_payload) "
                    "VALUES (:run, 'REVIEW', :day, 'query', 'duplicate', CAST(CAST(:day AS text) AS date), 'Trades', 'overlap', "
                    "CAST(:payload AS jsonb))"
                ), {**params, "payload": json.dumps(payload)})
                connection.execute(text(
                    "INSERT INTO pnl_snapshot_daily(account_id, report_date_local, instrument_id, position_qty, "
                    "realized_pnl, total_pnl, fees, currency, ingestion_run_id) "
                    "VALUES ('REVIEW', CAST(CAST(:day AS text) AS date), :instrument, 0, :expected, :expected, 2, 'USD', :run)"
                ), params)
        for fx_date, rate in (("2026-08-19", "1.2"), ("2026-08-20", "0"),
                              ("2026-08-21", "-1"), ("2026-08-22", "9")):
            connection.execute(text(
                "INSERT INTO event_fx(account_id, ingestion_run_id, source_raw_record_id, transaction_id, "
                "report_date_local, currency, functional_currency, fx_rate, fx_source) "
                "VALUES ('REVIEW', :run, :raw, :fx_date, CAST(CAST(:fx_date AS text) AS date), "
                "'EUR', 'USD', :rate, 'conversion_rates')"
            ), {"run": run, "raw": raw, "fx_date": fx_date, "rate": Decimal(rate)})
    row = repository.db_reconciliation_sources("REVIEW", date(2026, 8, 21), date(2026, 8, 21), None)[0]
    assert Decimal(row.broker_realized_pnl) == Decimal(row.realized_pnl) == expected_realized
    assert Decimal(row.broker_fees) == Decimal(row.fees) == 2
    with engine.begin() as connection:
        raw = connection.execute(text(
            "INSERT INTO raw_record(ingestion_run_id, account_id, period_key, flex_query_id, "
            "payload_sha256, report_date_local, section_name, source_row_ref, source_payload) "
            "VALUES (:run, 'REVIEW', '2026-08-20', 'query', 'cash', '2026-08-20', "
            "'CashTransactions', 'dividend', CAST(:payload AS jsonb)) RETURNING raw_record_id"
        ), {"run": run, "payload": json.dumps({"type": "DIV", "currency": "USD", "amount": "100",
                                               "withholdingTax": "15", "fees": "2"})}).scalar_one()
        params = {"instrument": instrument, "run": run, "raw": raw, "expected": expected_realized + 83}
        connection.execute(text(
            "INSERT INTO event_cashflow(account_id, instrument_id, ingestion_run_id, source_raw_record_id, "
            "transaction_id, cash_action, report_date_local, amount, withholding_tax, fees, currency) "
            "VALUES ('REVIEW', :instrument, :run, :raw, 'dividend', 'DIV', '2026-08-20', 100, 15, 2, 'USD')"
        ), params)
        connection.execute(text(
            "UPDATE pnl_snapshot_daily SET realized_pnl=:expected, total_pnl=:expected, fees=4, withholding_tax=15 "
            "WHERE instrument_id=:instrument"
        ), params)
    row = repository.db_reconciliation_sources("REVIEW", date(2026, 8, 21), date(2026, 8, 21), None)[0]
    assert Decimal(row.broker_realized_pnl) == Decimal(row.realized_pnl) == expected_realized + 83
    assert Decimal(row.broker_fees) == Decimal(row.fees) == 4
    assert Decimal(row.broker_withholding_tax) == Decimal(row.withholding_tax) == 15
