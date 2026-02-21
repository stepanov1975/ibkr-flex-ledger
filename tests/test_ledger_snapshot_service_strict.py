"""Regression tests for strict solid-valuation snapshot behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.db.interfaces import LedgerOpenPositionValuationRecord, LedgerTradeFillRecord
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
    ) -> None:
        self._trades = trades
        self._valuations = valuations
        self.position_requests = _SnapshotCapture(requests=[])
        self.snapshot_requests = _SnapshotCapture(requests=[])

    def db_ledger_trade_fill_list_for_account(self, account_id: str, through_report_date_local: str | None = None):
        """Return deterministic trade rows for one account/date query."""
        _ = (account_id, through_report_date_local)
        return self._trades

    def db_ledger_cashflow_list_for_account(self, account_id: str, through_report_date_local: str | None = None):
        """Return deterministic cashflow rows for one account/date query."""
        _ = (account_id, through_report_date_local)
        return []

    def db_ledger_open_position_valuation_list_for_run(self, account_id: str, ingestion_run_id: str):
        """Return deterministic OpenPositions valuation rows for one run."""
        _ = (account_id, ingestion_run_id)
        return self._valuations

    def db_position_lot_upsert_many(self, requests):
        """Capture position-lot upsert payload for assertions."""
        self.position_requests.requests = requests

    def db_pnl_snapshot_daily_upsert_many(self, requests):
        """Capture snapshot upsert payload for assertions."""
        self.snapshot_requests.requests = requests
def test_snapshot_marks_missing_when_open_position_has_no_solid_broker_valuation() -> None:
    """Mark snapshot row provisional and skip unrealized calculation when solid valuation is missing."""

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
        run_completed_at_utc="2026-02-20T18:00:00+00:00",
    )

    assert result.missing_solid_valuation_count == 1
    assert len(repository.snapshot_requests.requests) == 1
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.provisional is True
    assert snapshot.valuation_source == "missing_solid_broker_openpositions"
    assert Decimal(snapshot.unrealized_pnl) == Decimal("0")


def test_snapshot_uses_broker_unrealized_when_position_matches() -> None:
    """Use broker OpenPositions unrealized value as solid valuation source when positions match."""

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
        run_completed_at_utc="2026-02-20T18:00:00+00:00",
    )

    assert result.missing_solid_valuation_count == 0
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.provisional is False
    assert snapshot.valuation_source == "openpositions_fifo_unrealized"
    assert Decimal(snapshot.unrealized_pnl) == Decimal("200")
