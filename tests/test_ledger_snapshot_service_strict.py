"""Regression tests for strict solid-valuation snapshot behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.db.interfaces import (
    LedgerCashflowRecord,
    LedgerFxRateRecord,
    LedgerOpenPositionValuationRecord,
    LedgerTradeFillRecord,
)
from app.ledger.snapshot_service import StockLedgerSnapshotService


@dataclass
class _SnapshotCapture:
    requests: list


class _RepositoryStub:
    """Repository stub for strict snapshot-service behavior tests."""

    def __init__(
        self,
        trades: list[LedgerTradeFillRecord],
        valuations: list[LedgerOpenPositionValuationRecord],
        fx_rates: list[LedgerFxRateRecord] | None = None,
        cashflows: list[LedgerCashflowRecord] | None = None,
    ) -> None:
        self._trades = trades
        self._valuations = valuations
        self._fx_rates = fx_rates or []
        self._cashflows = cashflows or []
        self.position_requests = _SnapshotCapture(requests=[])
        self.snapshot_requests = _SnapshotCapture(requests=[])
        self.reconcile_call_count = 0

    def db_ledger_trade_fill_list_for_account(self, account_id: str, through_report_date_local: str | None = None):
        """Return deterministic trade rows for one account/date query."""
        _ = (account_id, through_report_date_local)
        return self._trades

    def db_ledger_cashflow_list_for_account(self, account_id: str, through_report_date_local: str | None = None):
        """Return deterministic cashflow rows for one account/date query."""
        _ = (account_id, through_report_date_local)
        return self._cashflows

    def db_ledger_open_position_valuation_list_for_run(self, account_id: str, ingestion_run_id: str):
        """Return deterministic OpenPositions valuation rows for one run."""
        _ = (account_id, ingestion_run_id)
        return self._valuations

    def db_ledger_fx_rate_list_for_account(self, account_id: str, through_report_date_local: str):
        """Return no conversion rows for USD-only fixtures."""
        _ = (account_id, through_report_date_local)
        return self._fx_rates

    def db_position_lot_upsert_many(self, requests):
        """Capture position-lot upsert payload for assertions."""
        self.position_requests.requests = requests

    def db_position_lot_reconcile_open(self, account_id: str, closed_at_utc: datetime, requests):
        """Capture the reconciled open-lot projection."""
        _ = (account_id, closed_at_utc)
        self.reconcile_call_count += 1
        self.position_requests.requests = requests

    def db_pnl_snapshot_daily_upsert_many(self, requests):
        """Capture snapshot upsert payload for assertions."""
        self.snapshot_requests.requests = requests


def test_snapshot_uses_last_trade_fallback_without_openpositions_valuation() -> None:
    """Use the frozen last-trade fallback when OpenPositions valuation is absent."""

    instrument_id = uuid4()
    trade = LedgerTradeFillRecord(
        event_trade_fill_id=uuid4(),
        account_id="U_TEST",
        instrument_id=instrument_id,
        source_raw_record_id=uuid4(),
        trade_timestamp_utc=datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc),
        report_date_local=date(2026, 2, 20),
        side="BUY",
        quantity="10",
        price="100",
        fees="0",
        commission="0",
        functional_currency="USD",
    )
    repository = _RepositoryStub(trades=[trade], valuations=[])
    service = StockLedgerSnapshotService(repository=repository)

    result = service.ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=str(uuid4()),
        report_date_local="2026-02-20",
    )

    assert result.missing_solid_valuation_count == 0
    assert len(repository.snapshot_requests.requests) == 1
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.provisional is False
    assert snapshot.valuation_source == "trades_last_trade_price"
    assert Decimal(snapshot.unrealized_pnl) == Decimal("0")


def test_snapshot_computes_unrealized_from_openpositions_mark_when_position_matches() -> None:
    """Compute economic unrealized PnL from the broker mark rather than copying broker PnL."""

    instrument_id = uuid4()
    trade = LedgerTradeFillRecord(
        event_trade_fill_id=uuid4(),
        account_id="U_TEST",
        instrument_id=instrument_id,
        source_raw_record_id=uuid4(),
        trade_timestamp_utc=datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc),
        report_date_local=date(2026, 2, 20),
        side="BUY",
        quantity="10",
        price="100",
        fees="0",
        commission="0",
        functional_currency="USD",
    )
    valuation = LedgerOpenPositionValuationRecord(
        instrument_id=instrument_id,
        position_qty="10",
        mark_price="120",
        broker_unrealized_pnl="200",
        report_date_local=date(2026, 2, 20),
    )
    repository = _RepositoryStub(trades=[trade], valuations=[valuation])
    service = StockLedgerSnapshotService(repository=repository)

    result = service.ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=str(uuid4()),
        report_date_local="2026-02-20",
    )

    assert result.missing_solid_valuation_count == 0
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.provisional is False
    assert snapshot.valuation_source == "openpositions_mark_price"
    assert Decimal(snapshot.unrealized_pnl) == Decimal("200")


def test_snapshot_converts_foreign_trade_and_mark_to_base_currency() -> None:
    """Apply trade-level FX before FIFO and emit USD-valued snapshot amounts."""

    instrument_id = uuid4()
    trade = LedgerTradeFillRecord(
        event_trade_fill_id=uuid4(),
        account_id="U_TEST",
        instrument_id=instrument_id,
        source_raw_record_id=uuid4(),
        trade_timestamp_utc=datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc),
        report_date_local=date(2026, 2, 20),
        side="BUY",
        quantity="10",
        price="100",
        fees="1",
        commission="0",
        functional_currency="USD",
        currency="EUR",
        fx_rate_to_base="1.2",
    )
    valuation = LedgerOpenPositionValuationRecord(
        instrument_id=instrument_id,
        position_qty="10",
        mark_price="110",
        broker_unrealized_pnl="999",
        report_date_local=date(2026, 2, 20),
    )
    fx_rate = LedgerFxRateRecord(
        report_date_local=date(2026, 2, 20),
        currency="EUR",
        functional_currency="USD",
        fx_rate="1.2",
        fx_source="conversion_rates",
        ingestion_run_id=uuid4(),
        source_raw_record_id=uuid4(),
    )
    repository = _RepositoryStub(trades=[trade], valuations=[valuation], fx_rates=[fx_rate])

    StockLedgerSnapshotService(repository=repository).ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=str(uuid4()),
        report_date_local="2026-02-20",
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.currency == "USD"
    assert snapshot.provisional is False
    assert Decimal(snapshot.cost_basis) == Decimal("1201.2")
    assert Decimal(snapshot.unrealized_pnl) == Decimal("118.8")
    assert "trade_fx_rate_to_base" in snapshot.fx_source
    assert "conversion_rates_exact" in snapshot.fx_source


def test_snapshot_includes_base_cashflow_amount_in_realized_pnl() -> None:
    """Include dividends and other canonical cashflow amounts in economic PnL."""

    instrument_id = uuid4()
    trade = LedgerTradeFillRecord(
        event_trade_fill_id=uuid4(),
        account_id="U_TEST",
        instrument_id=instrument_id,
        source_raw_record_id=uuid4(),
        trade_timestamp_utc=datetime(2026, 2, 20, 12, 0, tzinfo=timezone.utc),
        report_date_local=date(2026, 2, 20),
        side="BUY",
        quantity="1",
        price="100",
        fees="0",
        commission="0",
        functional_currency="USD",
    )
    cashflow = LedgerCashflowRecord(
        event_cashflow_id=uuid4(),
        account_id="U_TEST",
        instrument_id=instrument_id,
        report_date_local=date(2026, 2, 20),
        withholding_tax="1",
        fees="0.5",
        functional_currency="USD",
        amount="10",
        amount_in_base="10",
        currency="USD",
    )
    repository = _RepositoryStub(trades=[trade], valuations=[], cashflows=[cashflow])

    StockLedgerSnapshotService(repository=repository).ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=str(uuid4()),
        report_date_local="2026-02-20",
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert Decimal(snapshot.realized_pnl) == Decimal("8.5")
    assert Decimal(snapshot.total_pnl) == Decimal("8.5")


def test_snapshot_reconciles_empty_open_lot_projection_after_full_close() -> None:
    """Reconcile stale persisted lots even when the recomputed position is fully closed."""

    instrument_id = uuid4()
    common = {
        "account_id": "U_TEST",
        "instrument_id": instrument_id,
        "report_date_local": date(2026, 2, 20),
        "fees": "0",
        "commission": "0",
        "functional_currency": "USD",
    }
    trades = [
        LedgerTradeFillRecord(
            event_trade_fill_id=uuid4(), source_raw_record_id=uuid4(),
            trade_timestamp_utc=datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
            side="BUY", quantity="1", price="100", **common,
        ),
        LedgerTradeFillRecord(
            event_trade_fill_id=uuid4(), source_raw_record_id=uuid4(),
            trade_timestamp_utc=datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc),
            side="SELL", quantity="1", price="110", **common,
        ),
    ]
    repository = _RepositoryStub(trades=trades, valuations=[])

    StockLedgerSnapshotService(repository=repository).ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=str(uuid4()),
        report_date_local="2026-02-20",
    )

    assert repository.reconcile_call_count == 1
    assert repository.position_requests.requests == []
