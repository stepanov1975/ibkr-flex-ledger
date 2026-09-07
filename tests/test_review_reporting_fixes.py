"""Actual-pattern PostgreSQL regressions for expense reporting and valuation lineage."""
# ruff: noqa: F811

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.db.ledger_snapshot import SQLAlchemyLedgerSnapshotService
from app.ledger.snapshot_service import StockLedgerSnapshotService
from test_review_portfolio_regressions import portfolio_db  # noqa: F401


DAY = date(2026, 8, 21)


def _seed(connection, *, status="success"):
    instrument = connection.execute(text(
        "INSERT INTO instrument(account_id, conid, symbol, asset_category, currency) "
        "VALUES ('REVIEW', '1', 'TEST', 'STK', 'USD') RETURNING instrument_id"
    )).scalar_one()
    run, artifact = _artifact(connection, status=status)
    return instrument, run, artifact


def _artifact(connection, *, status="success"):
    run = connection.execute(text(
        "INSERT INTO ingestion_run(account_id, run_type, status, period_key, flex_query_id, started_at_utc) "
        "VALUES ('REVIEW', 'manual', :status, '2026-08-21', 'query', now()) RETURNING ingestion_run_id"
    ), {"status": status}).scalar_one()
    artifact = connection.execute(text(
        "INSERT INTO raw_artifact(ingestion_run_id, account_id, period_key, flex_query_id, payload_sha256, "
        "report_date_local, source_payload) VALUES (:run, 'REVIEW', '2026-08-21', 'query', :sha, "
        "'2026-08-21', :payload) RETURNING raw_artifact_id"
    ), {"run": run, "sha": str(run), "payload": b"<FlexStatement><OpenPositions /></FlexStatement>"}).scalar_one()
    return run, artifact


def _raw(connection, run, artifact, section, ref, payload):
    return connection.execute(text(
        "INSERT INTO raw_record(raw_artifact_id, ingestion_run_id, account_id, period_key, flex_query_id, "
        "payload_sha256, report_date_local, section_name, source_row_ref, source_payload) "
        "VALUES (:artifact, :run, 'REVIEW', '2026-08-21', 'query', :sha, '2026-08-21', :section, :ref, "
        "CAST(:payload AS jsonb)) RETURNING raw_record_id"
    ), {"run": run, "artifact": artifact, "sha": str(run), "section": section, "ref": ref,
        "payload": json.dumps(payload)}).scalar_one()


def _snapshot(connection, instrument, run, quantity=0):
    connection.execute(text(
        "INSERT INTO pnl_snapshot_daily(account_id, report_date_local, instrument_id, position_qty, "
        "realized_pnl, unrealized_pnl, total_pnl, currency, ingestion_run_id, valuation_source) "
        "VALUES ('REVIEW', '2026-08-21', :instrument, :quantity, 0, 0, 0, 'USD', :run, :source)"
    ), {"instrument": instrument, "run": run, "quantity": quantity,
        "source": "openpositions_mark_price" if quantity else "broker_position_absent"})


@pytest.mark.parametrize("currency,base,tax_credit,supplemental,expected_net,expected_tax,expected_fees", [
    ("USD", False, "0", False, "83", "15", "2"),
    ("USD", False, "5", False, "88", "10", "2"),
    ("USD", False, "0", True, "77", "17", "6"),
    ("EUR", True, "0", False, "99.6", "18", "2.4"),
    ("EUR", False, "5", False, "105.6", "12", "2.4"),
])
def test_standalone_cash_expenses_preserve_net_and_match_reconciliation(
    portfolio_db, currency, base, tax_credit, supplemental, expected_net, expected_tax, expected_fees,
):
    engine, repository = portfolio_db
    with engine.begin() as connection:
        instrument, run, artifact = _seed(connection)
        _raw(connection, run, artifact, "OpenPositions", "OpenPositions:section:1", {})
        for index, (action, amount) in enumerate([
            ("Dividends", "100"), ("Withholding Tax", "-15"), ("Other Fees", "-2"),
            ("Withholding Tax", tax_credit),
        ]):
            # Explicit fields remain additional canonical deductions, including on standalone rows.
            tax = "2" if supplemental and index == 1 else "0"
            fees = "4" if supplemental and index == 2 else "0"
            amount_base = Decimal(amount) * Decimal("1.2") if base else None
            raw = _raw(connection, run, artifact, "CashTransactions", str(index), {
                "conid": "1", "type": action, "currency": currency, "amount": amount,
                "withholdingTax": tax, "fees": fees,
            })
            connection.execute(text(
                "INSERT INTO event_cashflow(account_id, instrument_id, ingestion_run_id, source_raw_record_id, "
                "transaction_id, cash_action, report_date_local, amount, amount_in_base, withholding_tax, fees, "
                "currency, functional_currency) VALUES ('REVIEW', :instrument, :run, :raw, :id, :action, "
                "'2026-08-21', :amount, :base, :tax, :fees, :currency, 'USD')"
            ), {"instrument": instrument, "run": run, "raw": raw, "id": str(index), "action": action,
                "amount": Decimal(amount), "base": amount_base, "tax": Decimal(tax), "fees": Decimal(fees),
                "currency": currency})
        if currency == "EUR" and not base:
            connection.execute(text(
                "INSERT INTO event_fx(account_id, ingestion_run_id, source_raw_record_id, transaction_id, "
                "report_date_local, currency, functional_currency, fx_rate, fx_source) "
                "VALUES ('REVIEW', :run, :raw, 'fx', '2026-08-21', 'EUR', 'USD', 1.2, 'conversion_rates')"
            ), {"run": run, "raw": raw})
    StockLedgerSnapshotService(SQLAlchemyLedgerSnapshotService(engine)).ledger_snapshot_build_and_persist(
        "REVIEW", str(run), DAY.isoformat(), "USD"
    )
    row = repository.db_reconciliation_sources("REVIEW", DAY, DAY, instrument)[0]
    assert Decimal(row.realized_pnl) == Decimal(expected_net)
    assert Decimal(row.withholding_tax) == Decimal(expected_tax)
    assert Decimal(row.fees) == Decimal(expected_fees)
    assert Decimal(row.broker_realized_pnl) == Decimal(expected_net)
    assert Decimal(row.broker_withholding_tax) == Decimal(expected_tax)
    assert Decimal(row.broker_fees) == Decimal(expected_fees)
    assert row.provisional is False
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT amount FROM event_cashflow WHERE cash_action='Other Fees'")) == -2


@pytest.mark.parametrize("section,status,present_row,expected", [
    (True, "success", False, "0"),
    (False, "success", False, None),
    (True, "failed", False, None),
    (True, "success", True, None),
])
def test_absent_positions_are_zero_only_with_complete_snapshot_section(
    portfolio_db, section, status, present_row, expected,
):
    engine, repository = portfolio_db
    with engine.begin() as connection:
        instrument, run, artifact = _seed(connection, status=status)
        _snapshot(connection, instrument, run)
        if section:
            _raw(connection, run, artifact, "OpenPositions",
                 "OpenPositions:OpenPosition:1" if present_row else "OpenPositions:section:1",
                 {"conid": "1", "position": "N/A", "fifoPnlUnrealized": "N/A"} if present_row else {})
        # A newer unrelated complete artifact must never establish this snapshot's completeness.
        other_run, other_artifact = _artifact(connection)
        _raw(connection, other_run, other_artifact, "OpenPositions", "OpenPositions:section:1", {})
    row = repository.db_reconciliation_sources("REVIEW", DAY, DAY, instrument)[0]
    assert row.broker_position_qty == expected
    assert row.broker_unrealized_pnl == expected


def test_provenance_contains_exact_broker_only_snapshot_row_and_artifact_after_replay(portfolio_db):
    engine, repository = portfolio_db
    with engine.begin() as connection:
        instrument, run, artifact = _seed(connection)
        payload = {"conid": "1", "assetCategory": "STK", "currency": "USD", "position": "5", "markPrice": "10"}
        raw = _raw(connection, run, artifact, "OpenPositions", "OpenPositions:OpenPosition:1", payload)
        _snapshot(connection, instrument, run, quantity=5)
        replay_run, newer_artifact = _artifact(connection)
        _raw(connection, replay_run, newer_artifact, "OpenPositions", "OpenPositions:OpenPosition:1",
             {**payload, "markPrice": "99"})
        connection.execute(text(
            "UPDATE raw_artifact SET completed_ingestion_run_id=:replay WHERE raw_artifact_id=:artifact"
        ), {"artifact": artifact, "replay": replay_run})
    rows = repository.db_report_provenance("REVIEW", DAY, instrument)
    assert len(rows) == 1
    assert rows[0].event_type == "open_position"
    assert rows[0].source_raw_record_id == raw
    assert rows[0].source_payload == payload
    assert rows[0].raw_artifact_id == artifact
    assert rows[0].ingestion_run_id == run
    assert repository.db_report_provenance("REVIEW", date(2026, 8, 22), instrument) == []


@pytest.mark.parametrize("completion_status,expected", [("success", "0"), ("failed", None)])
def test_closed_position_completeness_checks_replay_completion_status(portfolio_db, completion_status, expected):
    engine, repository = portfolio_db
    with engine.begin() as connection:
        instrument, run, artifact = _seed(connection, status="failed")
        _snapshot(connection, instrument, run)
        _raw(connection, run, artifact, "OpenPositions", "OpenPositions:section:1", {})
        completion, _ = _artifact(connection, status=completion_status)
        connection.execute(text(
            "UPDATE raw_artifact SET completed_ingestion_run_id=:completion WHERE raw_artifact_id=:artifact"
        ), {"artifact": artifact, "completion": completion})
    row = repository.db_reconciliation_sources("REVIEW", DAY, DAY, instrument)[0]
    assert row.broker_position_qty == expected
    assert row.broker_unrealized_pnl == expected
