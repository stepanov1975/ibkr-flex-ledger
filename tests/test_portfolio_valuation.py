"""Verify portfolio valuation against published Flex imports."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.db.portfolio import SQLAlchemyPortfolioService
from app.ledger import snapshot_resolve_report_date_local
from test_end_to_end_seeded import _SEEDED_PAYLOAD
from test_ingestion_integrity_regressions import _harness, _replay, database as _database


database = _database
DAY = date(2026, 8, 21)


@pytest.mark.parametrize("ambiguous_row_detail", ["LOT", "SUMMARY"])
def test_valuation_requires_one_summary_per_instrument(database, ambiguous_row_detail):
    orchestrator, adapter, *_ = _harness(database)
    portfolio = SQLAlchemyPortfolioService(database)
    start = _SEEDED_PAYLOAD.index(b"<OpenPosition ")
    end = _SEEDED_PAYLOAD.index(b"</OpenPositions>")
    summary = _SEEDED_PAYLOAD[start:end].strip().replace(
        b"<OpenPosition ", b'<OpenPosition levelOfDetail="SUMMARY" multiplier="1" ',
    )
    lot = (b'<OpenPosition levelOfDetail="LOT" conid="900001" symbol="SEED" assetCategory="STK" '
           b'currency="USD" reportDate="20260821" position="1" markPrice="110" positionValue="110" '
           b'fxRateToBase="1" fifoPnlUnrealized="10" />')
    adapter.payload_bytes = _SEEDED_PAYLOAD[:start] + lot + summary + _SEEDED_PAYLOAD[end:]
    assert orchestrator.job_execute("ingestion_run").status == "success"
    report = portfolio.db_report_portfolio_summary("INTEGRITY")
    assert Decimal(report.estimated_net_liquidation_value_usd) == Decimal("250")
    snapshot, = portfolio.db_report_pnl_by_instrument("INTEGRITY", DAY, DAY, None)
    assert Decimal(snapshot.position_qty) == Decimal("2")
    assert not snapshot.provisional
    evidence = portfolio.db_report_provenance("INTEGRITY", DAY, snapshot.instrument_id)
    assert [row.source_payload["levelOfDetail"] for row in evidence if row.event_type == "open_position"] == ["SUMMARY"]

    # The lot stays at the same source row when the summary disappears.
    rows = lot if ambiguous_row_detail == "LOT" else lot.replace(b'"LOT"', b'"SUMMARY"') + summary
    adapter.payload_bytes = _SEEDED_PAYLOAD[:start] + rows + _SEEDED_PAYLOAD[end:]
    assert orchestrator.job_execute("ingestion_run").status == "success"
    report = portfolio.db_report_portfolio_summary("INTEGRITY")
    assert report.estimated_net_liquidation_value_usd is None
    assert report.total_profit_usd is None
    snapshot, = portfolio.db_report_pnl_by_instrument("INTEGRITY", DAY, DAY, None)
    assert snapshot.provisional
    assert Decimal(snapshot.position_qty) == Decimal("2")
    assert snapshot.cost_basis is None
    reconciliation, = portfolio.db_reconciliation_sources("INTEGRITY", DAY, DAY, None)
    assert reconciliation.broker_position_qty is None

    # A correction that restores the unique summary can reuse an unchanged row.
    adapter.payload_bytes = _SEEDED_PAYLOAD[:start] + lot + summary + _SEEDED_PAYLOAD[end:] + b"\n"
    assert orchestrator.job_execute("ingestion_run").status == "success"
    snapshot, = portfolio.db_report_pnl_by_instrument("INTEGRITY", DAY, DAY, None)
    assert not snapshot.provisional
    history = portfolio.db_report_stock_history("INTEGRITY", snapshot.instrument_id)
    assert history is not None and not history["stale"]


def test_broker_only_holding_with_unavailable_summary_can_be_closed(database):
    orchestrator, adapter, *_ = _harness(database)
    portfolio = SQLAlchemyPortfolioService(database)
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(
        b'<OpenPosition conid="900001"', b'<OpenPosition levelOfDetail="LOT" conid="900002"',
    )
    assert orchestrator.job_execute("ingestion_run").status == "success"
    broker_only = next(
        row for row in portfolio.db_report_pnl_by_instrument("INTEGRITY", DAY, DAY, None) if row.conid == "900002"
    )
    assert broker_only.provisional
    assert broker_only.cost_basis is None

    start = _SEEDED_PAYLOAD.index(b"<OpenPositions>")
    end = _SEEDED_PAYLOAD.index(b"</OpenPositions>") + len(b"</OpenPositions>")
    adapter.payload_bytes = _SEEDED_PAYLOAD[:start] + b"<OpenPositions/>" + _SEEDED_PAYLOAD[end:]
    assert orchestrator.job_execute("ingestion_run").status == "success"
    closed, = portfolio.db_report_pnl_by_instrument("INTEGRITY", DAY, DAY, broker_only.instrument_id)
    assert Decimal(closed.position_qty) == Decimal("0")
    assert not closed.provisional


def test_lot_detail_does_not_make_a_summary_valuation_stale(database):
    harness = _harness(database)
    orchestrator, adapter, *_ = harness
    portfolio = SQLAlchemyPortfolioService(database)
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(
        b'<OpenPosition conid="900001"', b'<OpenPosition levelOfDetail="SUMMARY" multiplier="1" conid="900001"',
    )
    assert orchestrator.job_execute("ingestion_run").status == "success"
    snapshot, = portfolio.db_report_pnl_by_instrument("INTEGRITY", DAY, DAY, None)

    # The added lot carries a different quantity from the unchanged summary.
    lot = (b'<OpenPosition levelOfDetail="LOT" conid="900001" symbol="SEED" assetCategory="STK" '
           b'currency="USD" reportDate="20260821" position="1" markPrice="110" positionValue="110" />')
    adapter.payload_bytes = adapter.payload_bytes.replace(b"</OpenPositions>", lot + b"</OpenPositions>")
    assert orchestrator.job_execute("ingestion_run").status == "success"
    period = snapshot_resolve_report_date_local(datetime.now(timezone.utc).isoformat())
    assert _replay(harness, period).status == "success"
    report = portfolio.db_report_stock_history("INTEGRITY", snapshot.instrument_id)
    assert report is not None and not report["stale"]
    assert Decimal(report["positions"][0]["position_qty"]) == Decimal("2")


def test_portfolio_report_reads_one_committed_import(database):
    orchestrator, adapter, *_ = _harness(database)
    portfolio = SQLAlchemyPortfolioService(database)
    adapter.payload_bytes = _SEEDED_PAYLOAD
    assert orchestrator.job_execute("ingestion_run").status == "success"
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b'endingCash="30"', b'endingCash="130"').replace(
        b'positionValue="220"', b'positionValue="240"',
    )
    published = False

    def publish_after_first_read(_connection, _cursor, statement, *_):
        nonlocal published
        if not published and "cash_values AS" in statement:
            published = True
            assert orchestrator.job_execute("ingestion_run").status == "success"

    event.listen(database, "after_cursor_execute", publish_after_first_read)
    try:
        concurrent = portfolio.db_report_portfolio_summary("INTEGRITY")
    finally:
        event.remove(database, "after_cursor_execute", publish_after_first_read)

    assert published
    assert Decimal(concurrent.estimated_net_liquidation_value_usd) == Decimal("250")
    after = portfolio.db_report_portfolio_summary("INTEGRITY")
    assert Decimal(after.estimated_net_liquidation_value_usd) == Decimal("370")


def test_portfolio_profit_only_uses_transfers_through_valuation_date(database):
    orchestrator, adapter, *_ = _harness(database)
    portfolio = SQLAlchemyPortfolioService(database)
    adapter.payload_bytes = _SEEDED_PAYLOAD
    assert orchestrator.job_execute("ingestion_run").status == "success"

    # The undated statement supplies transfers without replacing the dated valuation.
    additional_transfers = b"""<CashTransaction transactionID="9004" type="Deposits/Withdrawals"
      amount="25" currency="USD" reportDate="20260821" />
    <CashTransaction transactionID="9005" type="Deposits/Withdrawals"
      amount="50" currency="USD" reportDate="20260822" />
    <CashTransaction transactionID="9006" type="Deposits/Withdrawals"
      amount="10" currency="EUR" reportDate="20260823" />"""
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b' reportDate="20260821">', b'>', 1).replace(
        b"</CashTransactions>", additional_transfers + b"</CashTransactions>",
    )
    assert orchestrator.job_execute("ingestion_run").status == "success"
    summary = portfolio.db_report_portfolio_summary("INTEGRITY")

    assert summary.report_date_local == DAY
    assert Decimal(summary.estimated_net_liquidation_value_usd) == Decimal("250")
    assert Decimal(summary.total_profit_usd) == Decimal("125")
    assert Decimal(summary.profit_percent) == Decimal("100")
    assert summary.net_transfers_usd is None
    assert {row.currency: Decimal(row.net_transfers) for row in summary.transfer_summary_by_currency} == {
        "USD": Decimal("175"), "EUR": Decimal("10"),
    }
    assert len(summary.transfers) == 4
