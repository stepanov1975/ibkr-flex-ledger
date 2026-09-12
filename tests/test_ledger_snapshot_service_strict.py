"""Regression tests for strict solid-valuation snapshot behavior."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Generic, TypeVar, cast
from uuid import UUID, uuid4

import pytest

from app.db.interfaces import (
    LedgerCashflowRecord,
    LedgerCorporateActionRecord,
    LedgerFxRateRecord,
    LedgerOpenPositionValuationRecord,
    LedgerSnapshotRepositoryPort,
    LedgerTradeFillRecord,
    PnlSnapshotDailyUpsertRequest,
    PositionLotUpsertRequest,
)
from app.ledger.snapshot_service import StockLedgerSnapshotService


_RequestT = TypeVar("_RequestT")


@dataclass
class _SnapshotCapture(Generic[_RequestT]):
    requests: list[_RequestT]


class _RepositoryStub:
    """Repository stub for strict snapshot-service behavior tests."""

    def __init__(
        self,
        trades: list[LedgerTradeFillRecord],
        valuations: list[LedgerOpenPositionValuationRecord],
        fx_rates: list[LedgerFxRateRecord] | None = None,
        cashflows: list[LedgerCashflowRecord] | None = None,
        corporate_actions: list[LedgerCorporateActionRecord] | None = None,
        scope_ids: list[str] | None = None,
        instrument_currencies: list[str] | None = None,
        instrument_asset_categories: dict[str, str] | None = None,
        existing_snapshot_count: int = 1,
    ) -> None:
        self._trades = trades
        self._valuations = valuations
        self._fx_rates = fx_rates or []
        self._cashflows = cashflows or []
        self._corporate_actions = corporate_actions or []
        self._scope_ids = list(scope_ids or [])
        self._instrument_currencies = list(instrument_currencies or [])
        self._instrument_asset_categories = dict(instrument_asset_categories or {})
        self._existing_snapshot_count = existing_snapshot_count
        self.position_requests: _SnapshotCapture[PositionLotUpsertRequest] = _SnapshotCapture(requests=[])
        self.snapshot_requests: _SnapshotCapture[PnlSnapshotDailyUpsertRequest] = _SnapshotCapture(requests=[])
        self.reconcile_call_count = 0
        self.trade_instrument_ids: tuple[str, ...] | None = None
        self.cashflow_instrument_ids: tuple[str, ...] | None = None
        self.corp_action_instrument_ids: tuple[str, ...] | None = None
        self.open_position_instrument_ids: tuple[str, ...] | None = None
        self.reconciled_instrument_ids: tuple[str, ...] | None = None
        self.fx_currencies: tuple[str, ...] | None = None
        self.asset_category_instrument_ids: tuple[str, ...] | None = None
        self.read_call_count = 0

    def db_ledger_prior_holding_ids(self, account_id: str, report_date_local: str) -> list[str]:
        self.read_call_count += 1
        return []

    def db_ledger_instrument_ids_for_scope(
        self,
        account_id: str,
        conids: tuple[str, ...],
        currencies: tuple[str, ...],
    ) -> list[str]:
        """Return the configured deterministic instrument scope."""
        _ = (account_id, conids, currencies)
        self.read_call_count += 1
        return self._scope_ids

    def db_ledger_instrument_currency_list(self, instrument_ids: tuple[str, ...]) -> list[str]:
        """Return currencies for the configured instrument scope."""
        _ = instrument_ids
        self.read_call_count += 1
        return self._instrument_currencies

    def db_ledger_instrument_asset_category_map(
        self,
        account_id: str,
        instrument_ids: tuple[str, ...],
    ) -> dict[str, str]:
        """Return broker-eligibility metadata for selected instruments."""

        _ = account_id
        self.read_call_count += 1
        self.asset_category_instrument_ids = instrument_ids
        return {
            instrument_id: self._instrument_asset_categories.get(instrument_id, "STK")
            for instrument_id in instrument_ids
        }

    def db_ledger_trade_fill_list_for_account(
        self,
        account_id: str,
        through_report_date_local: str | None = None,
        instrument_ids: tuple[str, ...] | None = None,
    ) -> list[LedgerTradeFillRecord]:
        """Return deterministic trade rows for one account/date query."""
        _ = (account_id, through_report_date_local)
        self.read_call_count += 1
        self.trade_instrument_ids = instrument_ids
        return self._trades

    def db_ledger_cashflow_list_for_account(
        self,
        account_id: str,
        through_report_date_local: str | None = None,
        instrument_ids: tuple[str, ...] | None = None,
    ) -> list[LedgerCashflowRecord]:
        """Return deterministic cashflow rows for one account/date query."""
        _ = (account_id, through_report_date_local)
        self.read_call_count += 1
        self.cashflow_instrument_ids = instrument_ids
        return self._cashflows

    def db_ledger_open_position_valuation_list_for_run(
        self,
        account_id: str,
        ingestion_run_id: str,
        instrument_ids: tuple[str, ...] | None = None,
    ) -> list[LedgerOpenPositionValuationRecord]:
        """Return deterministic OpenPositions valuation rows for one run."""
        _ = (account_id, ingestion_run_id)
        self.read_call_count += 1
        self.open_position_instrument_ids = instrument_ids
        return self._valuations

    def db_ledger_fx_rate_list_for_account(
        self,
        account_id: str,
        through_report_date_local: str,
        currencies: tuple[str, ...] | None = None,
    ) -> list[LedgerFxRateRecord]:
        """Return no conversion rows for USD-only fixtures."""
        _ = (account_id, through_report_date_local)
        self.read_call_count += 1
        self.fx_currencies = currencies
        return self._fx_rates

    def db_ledger_corporate_action_list_for_account(
        self,
        account_id: str,
        through_report_date_local: str,
        instrument_ids: tuple[str, ...] | None = None,
    ) -> list[LedgerCorporateActionRecord]:
        """Return deterministic corporate-action adjustments."""
        _ = (account_id, through_report_date_local)
        self.read_call_count += 1
        self.corp_action_instrument_ids = instrument_ids
        return self._corporate_actions

    def db_position_lot_upsert_many(self, requests: list[PositionLotUpsertRequest]) -> None:
        """Capture position-lot upsert payload for assertions."""
        self.position_requests.requests = requests

    def db_ledger_projection_transaction(self) -> nullcontext[_RepositoryStub]:
        return nullcontext(self)

    def db_position_lot_reconcile(
        self,
        account_id: str,
        through_report_date_local: str,
        requests: list[PositionLotUpsertRequest],
        instrument_ids: tuple[str, ...] | None = None,
    ) -> int:
        """Capture the reconciled lot history."""
        _ = (account_id, through_report_date_local)
        self.reconciled_instrument_ids = instrument_ids
        self.reconcile_call_count += 1
        self.position_requests.requests = requests

        return len(requests)

    def db_pnl_snapshot_daily_upsert_many(
        self,
        requests: list[PnlSnapshotDailyUpsertRequest],
    ) -> None:
        """Capture snapshot upsert payload for assertions."""
        self.snapshot_requests.requests = requests

    def db_pnl_snapshot_daily_count(
        self,
        account_id: str,
        report_date_from: str | None = None,
        report_date_to: str | None = None,
    ) -> int:
        """Return whether the requested report date already has a baseline."""

        _ = (account_id, report_date_from, report_date_to)
        self.read_call_count += 1
        return self._existing_snapshot_count


def _snapshot_service(repository: _RepositoryStub) -> StockLedgerSnapshotService:
    """Inject the deliberately partial repository stub at the test boundary."""

    return StockLedgerSnapshotService(
        repository=cast(LedgerSnapshotRepositoryPort, repository)
    )


def _trade(
    instrument_id: UUID,
    side: str,
    quantity: str,
    price: str,
    *,
    minute: int = 0,
    asset_category: str = "STK",
    multiplier: str | None = None,
    close_price: str | None = None,
) -> LedgerTradeFillRecord:
    return LedgerTradeFillRecord(
        event_trade_fill_id=uuid4(),
        account_id="U_TEST",
        instrument_id=instrument_id,
        source_raw_record_id=uuid4(),
        trade_timestamp_utc=datetime(2026, 8, 20, 10, minute, tzinfo=timezone.utc),
        report_date_local=date(2026, 8, 20),
        side=side,
        quantity=quantity,
        price=price,
        fees="0",
        commission="0",
        functional_currency="USD",
        currency="USD",
        asset_category=asset_category,
        multiplier=multiplier,
        close_price=close_price,
    )


def _broker_position(
    instrument_id: UUID,
    position: str,
    *,
    asset_category: str = "STK",
    currency: str = "USD",
    mark: str | None = "12",
    cost: str | None = "1000",
    unrealized: str | None = "200",
    fx: str | None = "1",
    multiplier: str | None = "1",
) -> LedgerOpenPositionValuationRecord:
    return LedgerOpenPositionValuationRecord(
        instrument_id=instrument_id,
        asset_category=asset_category,
        currency=currency,
        position_qty=position,
        mark_price=mark,
        cost_basis_money=cost,
        broker_unrealized_pnl=unrealized,
        fx_rate_to_base=fx,
        multiplier=multiplier,
        report_date_local=date(2026, 8, 20),
    )


def test_snapshot_uses_last_trade_fallback_without_completed_openpositions() -> None:
    """Use the frozen last-trade fallback outside a completed broker artifact."""

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
    service = _snapshot_service(repository)

    result = service.ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=None,
        report_date_local="2026-02-20",
        functional_currency="USD",
    )

    assert result.missing_solid_valuation_count == 0
    assert len(repository.snapshot_requests.requests) == 1
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.provisional is True
    assert snapshot.valuation_source == "trades_last_trade_price"
    assert Decimal(snapshot.unrealized_pnl) == Decimal("0")


def test_snapshot_uses_broker_mark_when_position_matches() -> None:
    """Compute exact-match unrealized PnL from broker market value and FIFO cost."""

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
        asset_category="STK",
        currency="USD",
        position_qty="10",
        mark_price="120",
        cost_basis_money=None,
        broker_unrealized_pnl="999",
        fx_rate_to_base=None,
        multiplier="1",
        report_date_local=date(2026, 2, 20),
    )
    repository = _RepositoryStub(trades=[trade], valuations=[valuation])
    service = _snapshot_service(repository)

    result = service.ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=str(uuid4()),
        report_date_local="2026-02-20",
        functional_currency="USD",
    )

    assert result.missing_solid_valuation_count == 0
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.provisional is False
    assert snapshot.valuation_source == "openpositions_mark_price"
    assert Decimal(snapshot.unrealized_pnl) == Decimal("200")


def test_snapshot_converts_foreign_trade_and_broker_mark_to_base_currency() -> None:
    """Apply trade and broker-mark FX before emitting USD-valued snapshot amounts."""

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
        asset_category="STK",
        currency="EUR",
        position_qty="10",
        mark_price="110",
        cost_basis_money=None,
        broker_unrealized_pnl="999",
        fx_rate_to_base=None,
        multiplier="1",
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

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=str(uuid4()),
        report_date_local="2026-02-20",
        functional_currency="USD",
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.currency == "USD"
    assert snapshot.provisional is False
    assert snapshot.cost_basis is not None
    assert snapshot.fx_source is not None
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

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=str(uuid4()),
        report_date_local="2026-02-20",
        functional_currency="USD",
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert Decimal(snapshot.realized_pnl) == Decimal("8.5")
    assert Decimal(snapshot.total_pnl) == Decimal("8.5")


def test_snapshot_restates_pre_split_lots_on_current_quantity_basis() -> None:
    """Apply deterministic split factor to pre-action quantity and unit basis."""

    instrument_id = uuid4()
    trade = LedgerTradeFillRecord(
        event_trade_fill_id=uuid4(), account_id="U_TEST", instrument_id=instrument_id,
        source_raw_record_id=uuid4(), trade_timestamp_utc=datetime(2026, 2, 19, 12, 0, tzinfo=timezone.utc),
        report_date_local=date(2026, 2, 19), side="BUY", quantity="10", price="100", fees="0", commission="0",
        functional_currency="USD",
    )
    action = LedgerCorporateActionRecord(
        instrument_id=instrument_id, report_date_local=date(2026, 2, 20),
        action_type="FORWARDSPLIT", adjustment_factor="2",
    )
    valuation = LedgerOpenPositionValuationRecord(
        instrument_id=instrument_id, asset_category="STK", currency="USD", position_qty="20", mark_price="60",
        cost_basis_money=None, broker_unrealized_pnl="200", fx_rate_to_base=None, multiplier="1",
        report_date_local=date(2026, 2, 20),
    )
    repository = _RepositoryStub(trades=[trade], valuations=[valuation], corporate_actions=[action])

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=str(uuid4()),
        report_date_local="2026-02-20",
        functional_currency="USD",
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert Decimal(snapshot.position_qty) == Decimal("20")
    assert snapshot.cost_basis is not None
    assert Decimal(snapshot.cost_basis) == Decimal("1000")
    assert Decimal(snapshot.unrealized_pnl) == Decimal("200")


def test_snapshot_reconciles_empty_open_lot_projection_after_full_close() -> None:
    """Reconcile stale persisted lots even when the recomputed position is fully closed."""

    instrument_id = uuid4()
    trades = [
        LedgerTradeFillRecord(
            event_trade_fill_id=uuid4(),
            account_id="U_TEST",
            instrument_id=instrument_id,
            source_raw_record_id=uuid4(),
            trade_timestamp_utc=datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc),
            report_date_local=date(2026, 2, 20),
            side="BUY",
            quantity="1",
            price="100",
            fees="0",
            commission="0",
            functional_currency="USD",
        ),
        LedgerTradeFillRecord(
            event_trade_fill_id=uuid4(),
            account_id="U_TEST",
            instrument_id=instrument_id,
            source_raw_record_id=uuid4(),
            trade_timestamp_utc=datetime(2026, 2, 20, 11, 0, tzinfo=timezone.utc),
            report_date_local=date(2026, 2, 20),
            side="SELL",
            quantity="1",
            price="110",
            fees="0",
            commission="0",
            functional_currency="USD",
        ),
    ]
    repository = _RepositoryStub(trades=trades, valuations=[])

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=str(uuid4()),
        report_date_local="2026-02-20",
        functional_currency="USD",
    )

    assert repository.reconcile_call_count == 1
    assert len(repository.position_requests.requests) == 1
    assert repository.position_requests.requests[0].status == "closed"
    assert repository.position_requests.requests[0].remaining_quantity == "0"


def test_snapshot_build_limits_reads_and_writes_to_resolved_scope() -> None:
    """Propagate one resolved instrument scope through every scoped read and write."""

    instrument_id = "00000000-0000-0000-0000-000000000010"
    trade = LedgerTradeFillRecord(
        event_trade_fill_id=uuid4(),
        account_id="U1",
        instrument_id=cast(UUID, instrument_id),
        source_raw_record_id=uuid4(),
        trade_timestamp_utc=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
        report_date_local=date(2026, 8, 21),
        side="BUY",
        quantity="1",
        price="100",
        fees="0",
        commission="0",
        functional_currency="USD",
        currency="EUR",
    )
    unrelated_trade = LedgerTradeFillRecord(
        event_trade_fill_id=uuid4(),
        account_id="U1",
        instrument_id=cast(UUID, "00000000-0000-0000-0000-000000000099"),
        source_raw_record_id=uuid4(),
        trade_timestamp_utc=datetime(2026, 8, 21, 13, 0, tzinfo=timezone.utc),
        report_date_local=date(2026, 8, 21),
        side="BUY",
        quantity="1",
        price="100",
        fees="0",
        commission="0",
        functional_currency="USD",
        currency="EUR",
    )
    cashflow = LedgerCashflowRecord(
        event_cashflow_id=uuid4(),
        account_id="U1",
        instrument_id=cast(UUID, instrument_id),
        report_date_local=date(2026, 8, 21),
        withholding_tax="0",
        fees="0",
        functional_currency="JPY",
        amount="10",
        currency="CHF",
    )
    repository = _RepositoryStub(
        trades=[trade, unrelated_trade],
        valuations=[],
        cashflows=[cashflow],
        scope_ids=[instrument_id],
        instrument_currencies=["GBP"],
    )

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        account_id="U1",
        ingestion_run_id="00000000-0000-0000-0000-000000000001",
        report_date_local="2026-08-21",
        functional_currency="USD",
        affected_conids=frozenset({"100"}),
        affected_currencies=frozenset({"AUD"}),
    )

    expected = (instrument_id,)
    assert repository.trade_instrument_ids == expected
    assert repository.cashflow_instrument_ids == expected
    assert repository.corp_action_instrument_ids == expected
    assert repository.open_position_instrument_ids == expected
    assert repository.asset_category_instrument_ids == expected
    assert repository.reconciled_instrument_ids == expected
    assert repository.fx_currencies == ("AUD", "CHF", "EUR", "GBP", "JPY", "USD")
    assert [request.instrument_id for request in repository.position_requests.requests] == [instrument_id]
    assert [request.instrument_id for request in repository.snapshot_requests.requests] == [instrument_id]


def test_snapshot_first_build_for_report_date_ignores_incremental_scope() -> None:
    """Build every instrument when the report date has no complete baseline yet."""

    scoped_instrument_id = "00000000-0000-0000-0000-000000000010"
    closed_instrument_id = "00000000-0000-0000-0000-000000000099"
    repository = _RepositoryStub(
        trades=[
            _trade(cast(UUID, scoped_instrument_id), "BUY", "1", "100"),
            _trade(cast(UUID, closed_instrument_id), "BUY", "1", "50"),
            _trade(cast(UUID, closed_instrument_id), "SELL", "1", "60", minute=1),
        ],
        valuations=[],
        scope_ids=[scoped_instrument_id],
        existing_snapshot_count=0,
    )

    result = _snapshot_service(repository).ledger_snapshot_build_and_persist(
        account_id="U1",
        ingestion_run_id="00000000-0000-0000-0000-000000000001",
        report_date_local="2026-08-21",
        functional_currency="USD",
        affected_conids=frozenset({"100"}),
        affected_currencies=frozenset(),
    )

    assert result.full_rebuild_reason == "missing_report_date_baseline"
    assert repository.trade_instrument_ids is None
    assert repository.reconciled_instrument_ids is None
    assert {request.instrument_id for request in repository.snapshot_requests.requests} == {
        scoped_instrument_id,
        closed_instrument_id,
    }
    closed_snapshot = next(
        request
        for request in repository.snapshot_requests.requests
        if request.instrument_id == closed_instrument_id
    )
    assert Decimal(closed_snapshot.realized_pnl) == Decimal("10")


def test_snapshot_empty_scope_builds_full_when_report_date_has_no_baseline() -> None:
    """Carry closed-instrument P&L onto a new date even without changed rows."""

    closed_instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[
            _trade(closed_instrument_id, "BUY", "1", "50"),
            _trade(closed_instrument_id, "SELL", "1", "60", minute=1),
        ],
        valuations=[],
        existing_snapshot_count=0,
    )

    result = _snapshot_service(repository).ledger_snapshot_build_and_persist(
        account_id="U1",
        ingestion_run_id="00000000-0000-0000-0000-000000000001",
        report_date_local="2026-08-21",
        functional_currency="USD",
        affected_conids=frozenset(),
        affected_currencies=frozenset(),
    )

    assert result.full_rebuild_reason == "missing_report_date_baseline"
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.instrument_id == str(closed_instrument_id)
    assert Decimal(snapshot.realized_pnl) == Decimal("10")


@pytest.mark.parametrize(
    ("fees", "commission"),
    [("-5", "0"), ("0", "-5")],
)
def test_snapshot_normalizes_negative_ibkr_trade_charges_as_cost(
    fees: str,
    commission: str,
) -> None:
    """Treat IBKR's signed fees and commission as costs in FIFO basis and P&L."""

    instrument_id = uuid4()
    trade = replace(
        _trade(instrument_id, "BUY", "10", "100"),
        fees=fees,
        commission=commission,
    )
    repository = _RepositoryStub(
        trades=[trade],
        valuations=[_broker_position(
            instrument_id,
            "10",
            mark="120",
            cost="1005",
            unrealized="195",
        )],
    )

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.cost_basis is not None
    assert Decimal(snapshot.cost_basis) == Decimal("1005")
    assert Decimal(snapshot.unrealized_pnl) == Decimal("195")
    assert Decimal(snapshot.total_pnl) == Decimal("195")
    assert Decimal(snapshot.fees) == Decimal("5")


def test_snapshot_normalized_trade_charges_reduce_realized_pnl() -> None:
    """Apply opening and closing IBKR charges as realized economic costs."""

    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[
            replace(
                _trade(instrument_id, "BUY", "10", "100"),
                commission="-5",
            ),
            replace(
                _trade(instrument_id, "SELL", "10", "110", minute=1),
                commission="-5",
            ),
        ],
        valuations=[],
    )

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", None, "2026-08-20", "USD"
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert Decimal(snapshot.realized_pnl) == Decimal("90")
    assert Decimal(snapshot.total_pnl) == Decimal("90")
    assert Decimal(snapshot.fees) == Decimal("10")


def test_snapshot_build_empty_scope_is_noop() -> None:
    """Check for a baseline, then avoid semantic reads for an empty scope."""

    repository = _RepositoryStub(trades=[], valuations=[])

    result = _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U1",
        "00000000-0000-0000-0000-000000000001",
        "2026-08-21",
        "USD",
        frozenset(),
        frozenset(),
    )

    assert result.snapshot_row_count == 0
    assert result.position_lot_row_count == 0
    assert repository.read_call_count == 1
    assert repository.reconcile_call_count == 0


def test_snapshot_build_none_scope_retains_full_reads() -> None:
    """Keep the existing full-history read and reconciliation contract by default."""

    repository = _RepositoryStub(trades=[], valuations=[])

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U1",
        "00000000-0000-0000-0000-000000000001",
        "2026-08-21",
        "USD",
    )

    assert repository.trade_instrument_ids is None
    assert repository.cashflow_instrument_ids is None
    assert repository.corp_action_instrument_ids is None
    assert repository.open_position_instrument_ids is None
    assert repository.reconciled_instrument_ids is None
    assert repository.fx_currencies is None


def test_snapshot_uses_broker_quantity_and_cost_when_fifo_mismatches() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(instrument_id, "SELL", "200", "11")],
        valuations=[_broker_position(instrument_id, "0", cost=None, unrealized="0")],
    )
    result = _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "0"
    assert snapshot.cost_basis is None
    assert snapshot.unrealized_pnl == "0"
    assert snapshot.provisional is True
    assert result.broker_position_mismatch_count == 1


def test_snapshot_mismatch_uses_broker_cost_and_preserves_realized_pnl() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[
            _trade(instrument_id, "BUY", "10", "10"),
            _trade(instrument_id, "SELL", "4", "12", minute=1),
        ],
        valuations=[_broker_position(instrument_id, "5", cost="55", unrealized="5")],
    )
    result = _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "5"
    assert snapshot.cost_basis == "55"
    assert snapshot.realized_pnl == "8"
    assert snapshot.provisional is True
    assert result.broker_position_mismatch_count == 1


def test_snapshot_treats_broker_absence_as_zero_without_synthetic_lot() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(instrument_id, "SELL", "200", "11")],
        valuations=[],
    )
    result = _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    assert repository.snapshot_requests.requests[0].position_qty == "0"
    assert repository.snapshot_requests.requests[0].provisional is True
    assert len(repository.position_requests.requests) == 1
    assert result.broker_absent_nonzero_fifo_count == 1


@pytest.mark.parametrize("asset_category", ["CASH", "FX"])
def test_snapshot_completed_artifact_preserves_cash_fx_event_position(
    asset_category: str,
) -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(
            instrument_id,
            "BUY",
            "2",
            "10",
            asset_category=asset_category,
        )],
        valuations=[],
        instrument_asset_categories={str(instrument_id): asset_category},
    )

    result = _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "2"
    assert snapshot.provisional is True
    assert result.broker_position_match_count == 0
    assert result.broker_position_mismatch_count == 0
    assert result.broker_only_position_count == 0
    assert result.broker_absent_nonzero_fifo_count == 0


def test_snapshot_creates_broker_only_option_with_contract_valuation() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[],
        valuations=[_broker_position(
            instrument_id,
            "-1",
            asset_category="OPT",
            mark="2.21",
            cost="-28",
            unrealized="-193",
            multiplier="100",
        )],
    )
    result = _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "-1"
    assert snapshot.cost_basis == "-28"
    assert snapshot.realized_pnl == "0"
    assert snapshot.unrealized_pnl == "-193"
    assert snapshot.provisional is True
    assert repository.position_requests.requests == []
    assert result.broker_only_position_count == 1


def test_snapshot_exact_match_keeps_fifo_cost_and_uses_economic_unrealized() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(instrument_id, "BUY", "10", "10")],
        valuations=[_broker_position(
            instrument_id, "10", mark="12", cost="100", unrealized="200"
        )],
    )
    result = _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "10"
    assert snapshot.cost_basis == "100"
    assert snapshot.unrealized_pnl == "20"
    assert snapshot.valuation_source == "openpositions_mark_price"
    assert snapshot.provisional is False
    assert result.broker_position_match_count == 1


@pytest.mark.parametrize(
    ("currency", "mark", "multiplier", "fx"),
    [
        ("USD", None, "1", "1"),
        ("USD", "12", None, "1"),
        ("EUR", "12", "1", None),
    ],
)
def test_snapshot_exact_match_with_missing_market_input_is_provisional(
    currency: str,
    mark: str | None,
    multiplier: str | None,
    fx: str | None,
) -> None:
    instrument_id = uuid4()
    trade = _trade(instrument_id, "BUY", "10", "10")
    trade = replace(
        trade,
        currency=currency,
        fx_rate_to_base="1" if currency == "EUR" else None,
    )
    repository = _RepositoryStub(
        trades=[trade],
        valuations=[_broker_position(
            instrument_id,
            "10",
            currency=currency,
            mark=mark,
            cost="100",
            unrealized="200",
            fx=fx,
            multiplier=multiplier,
        )],
    )

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.unrealized_pnl == "0"
    assert snapshot.valuation_source == "EOD_MARK_MISSING_ALL_SOURCES"
    assert snapshot.provisional is True


def test_snapshot_preserves_missing_optional_broker_values() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[],
        valuations=[_broker_position(
            instrument_id, "5", mark=None, cost=None, unrealized=None
        )],
    )
    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "5"
    assert snapshot.cost_basis is None
    assert snapshot.unrealized_pnl == "0"
    assert snapshot.provisional is True


def test_snapshot_converts_broker_cost_and_unrealized_to_functional_currency() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[],
        valuations=[_broker_position(
            instrument_id,
            "10",
            currency="EUR",
            cost="100",
            unrealized="20",
            fx="1.2",
        )],
    )
    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.cost_basis == "120.0"
    assert snapshot.unrealized_pnl == "24.0"


def test_snapshot_includes_instrument_cashflow_without_trade_history() -> None:
    instrument_id = uuid4()
    cashflow = LedgerCashflowRecord(
        event_cashflow_id=uuid4(),
        account_id="U_TEST",
        instrument_id=instrument_id,
        report_date_local=date(2026, 8, 20),
        withholding_tax="0",
        fees="0",
        functional_currency="USD",
        amount="25",
        amount_in_base="25",
        currency="USD",
    )
    repository = _RepositoryStub(trades=[], valuations=[], cashflows=[cashflow])
    result = _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "0"
    assert snapshot.realized_pnl == "25"
    assert result.broker_position_match_count == 0
    assert result.broker_position_mismatch_count == 0
    assert result.broker_only_position_count == 0
    assert result.broker_absent_nonzero_fifo_count == 0


def test_snapshot_explicit_functional_currency_overrides_cashflow_base_currency() -> None:
    instrument_id = uuid4()
    cashflow = LedgerCashflowRecord(
        event_cashflow_id=uuid4(),
        account_id="U_TEST",
        instrument_id=instrument_id,
        report_date_local=date(2026, 8, 20),
        withholding_tax="0",
        fees="0",
        functional_currency="EUR",
        amount="25",
        amount_in_base="30",
        currency="USD",
    )
    repository = _RepositoryStub(trades=[], valuations=[], cashflows=[cashflow])

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.currency == "USD"
    assert snapshot.realized_pnl == "25"
    assert snapshot.fx_source is not None
    assert "cashflow_amount_in_base" not in snapshot.fx_source


def test_snapshot_option_mark_fallback_applies_contract_multiplier() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[],
        valuations=[_broker_position(
            instrument_id,
            "-1",
            asset_category="OPT",
            mark="2.21",
            cost="-28",
            unrealized=None,
            multiplier="100",
        )],
    )
    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    assert repository.snapshot_requests.requests[0].unrealized_pnl == "-193.00"


def test_snapshot_option_fifo_applies_execution_multiplier_to_realized_pnl() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[
            _trade(
                instrument_id,
                "SELL",
                "2",
                "0.60",
                asset_category="OPT",
                multiplier="100",
            ),
            _trade(
                instrument_id,
                "BUY",
                "2",
                "0",
                minute=1,
                asset_category="OPT",
                multiplier="100",
            ),
        ],
        valuations=[],
    )

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.realized_pnl == "120.00"
    assert len(repository.position_requests.requests) == 1
    assert repository.position_requests.requests[0].status == "closed"
    assert repository.position_requests.requests[0].remaining_quantity == "0"


def test_snapshot_option_close_mark_applies_execution_multiplier() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(
            instrument_id,
            "BUY",
            "1",
            "0.50",
            asset_category="OPT",
            multiplier="100",
            close_price="0.60",
        )],
        valuations=[],
    )

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", None, "2026-08-20", "USD"
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.cost_basis == "50.00"
    assert snapshot.unrealized_pnl == "10.00"


@pytest.mark.parametrize("multiplier", [None, "0", "-100"])
def test_snapshot_rejects_missing_or_nonpositive_option_execution_multiplier(
    multiplier: str | None,
) -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(
            instrument_id,
            "BUY",
            "1",
            "0.50",
            asset_category="OPT",
            multiplier=multiplier,
        )],
        valuations=[],
    )

    with pytest.raises(ValueError, match="OPT trade multiplier must be positive"):
        _snapshot_service(repository).ledger_snapshot_build_and_persist(
            "U_TEST", None, "2026-08-20", "USD"
        )


def test_snapshot_non_option_fifo_uses_positive_provided_multiplier() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[
            _trade(instrument_id, "BUY", "1", "10", multiplier="2"),
            _trade(instrument_id, "SELL", "1", "12", minute=1, multiplier="2"),
        ],
        valuations=[],
    )

    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", None, "2026-08-20", "USD"
    )

    assert repository.snapshot_requests.requests[0].realized_pnl == "4"


def test_snapshot_exact_short_match_keeps_signed_fifo_cost() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(instrument_id, "SELL", "2", "11")],
        valuations=[_broker_position(instrument_id, "-2", cost="-20", unrealized="-2")],
    )
    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    assert repository.snapshot_requests.requests[0].cost_basis == "-22"


@pytest.mark.parametrize("raw_ids", [(3, 1, 2), (1, 3, 2)])
def test_snapshot_fifo_preserves_numeric_transaction_order(raw_ids: tuple[int, int, int]) -> None:
    instrument_id = uuid4()
    trades = [
        replace(
            _trade(instrument_id, side, "1", price),
            transaction_id=transaction_id,
            source_raw_record_id=UUID(int=raw_id),
        )
        for side, price, transaction_id, raw_id in zip(
            ("BUY", "BUY", "SELL"), ("10", "20", "30"), ("2", "10", "11"), raw_ids, strict=True
        )
    ]
    repository = _RepositoryStub(trades=trades, valuations=[])
    _snapshot_service(repository).ledger_snapshot_build_and_persist("U_TEST", None, "2026-08-20", "USD")
    snapshot = repository.snapshot_requests.requests[0]
    assert Decimal(snapshot.realized_pnl) == Decimal("20")
    assert Decimal(snapshot.cost_basis or "0") == Decimal("20")


@pytest.mark.parametrize("factor", ["2", "0.5"])
def test_snapshot_historical_fallback_mark_is_split_adjusted(factor: str) -> None:
    instrument_id = uuid4()
    trade = _trade(instrument_id, "BUY", "10", "100")
    action = LedgerCorporateActionRecord(
        instrument_id=instrument_id, report_date_local=date(2026, 8, 21),
        action_type="FORWARDSPLIT", adjustment_factor=factor,
    )
    repository = _RepositoryStub(trades=[trade], valuations=[], corporate_actions=[action])
    _snapshot_service(repository).ledger_snapshot_build_and_persist("U_TEST", None, "2026-08-21", "USD")
    snapshot = repository.snapshot_requests.requests[0]
    assert Decimal(snapshot.position_qty) == Decimal("10") * Decimal(factor)
    assert Decimal(snapshot.cost_basis or "0") == Decimal("1000")
    assert Decimal(snapshot.unrealized_pnl) == Decimal("0")
    assert snapshot.provisional is True


@pytest.mark.parametrize(
    ("commission_currency", "commission_fx", "expected_fees", "provisional"),
    [("USD", None, "3.4", False), ("GBP", "1.5", "3.9", False), ("GBP", None, "2.4", True)],
)
def test_snapshot_converts_commission_currency_independently(
    commission_currency: str, commission_fx: str | None, expected_fees: str, provisional: bool,
) -> None:
    instrument_id = uuid4()
    trade = replace(
        _trade(instrument_id, "BUY", "10", "100"), currency="EUR", fx_rate_to_base="1.2",
        fees="-2", commission="-1", commission_currency=commission_currency,
    )
    fx_rates = [] if commission_fx is None else [LedgerFxRateRecord(
        report_date_local=date(2026, 8, 20), currency=commission_currency, functional_currency="USD",
        fx_rate=commission_fx, fx_source="test", ingestion_run_id=uuid4(), source_raw_record_id=uuid4(),
    )]
    repository = _RepositoryStub(
        trades=[trade], valuations=[_broker_position(instrument_id, "10", currency="EUR", mark="100", fx="1.2")],
        fx_rates=fx_rates, scope_ids=[str(instrument_id)], instrument_currencies=["EUR"],
    )
    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD", affected_conids=frozenset({"123"}),
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert Decimal(snapshot.fees) == Decimal(expected_fees)
    assert Decimal(snapshot.cost_basis or "0") == Decimal("1200") + Decimal(expected_fees)
    assert Decimal(snapshot.unrealized_pnl) == -Decimal(expected_fees)
    assert snapshot.provisional is provisional
    assert repository.fx_currencies is not None and commission_currency in repository.fx_currencies
    assert ("FX_RATE_MISSING_ALL_SOURCES" in (snapshot.fx_source or "")) is provisional


def test_snapshot_security_transfer_expands_scope_and_preserves_lot_origin():
    from app.db import interfaces
    old, new, unrelated = uuid4(), uuid4(), uuid4()
    opening = replace(_trade(old, "BUY", "8", "10"), report_date_local=date(2026, 8, 19),
                      trade_timestamp_utc=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    sale = replace(_trade(new, "SELL", "3", "30"), report_date_local=date(2026, 8, 21),
                   trade_timestamp_utc=datetime(2026, 8, 21, 12, tzinfo=timezone.utc))
    movement = interfaces.LedgerSecurityMovementRecord(
        event_corp_action_id=uuid4(), source_raw_record_id=uuid4(), source_instrument_id=old,
        destination_instrument_id=new, report_date_local=date(2026, 8, 20), quantity="8",
        cost_basis=None, currency="USD",
    )
    repository = _RepositoryStub(trades=[opening, sale], valuations=[], scope_ids=[str(new)])
    repository.db_ledger_security_movement_list_for_account = lambda **kwargs: [movement]
    _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", None, "2026-08-21", "USD", affected_conids=frozenset({"new"}),
    )
    assert set(repository.trade_instrument_ids) == {str(old), str(new)}
    assert set(repository.reconciled_instrument_ids) == {str(old), str(new)}
    snapshots = {row.instrument_id: row for row in repository.snapshot_requests.requests}
    assert Decimal(snapshots[str(old)].position_qty) == 0
    assert Decimal(snapshots[str(new)].position_qty) == 5
    assert Decimal(snapshots[str(new)].realized_pnl) == 60
    assert str(unrelated) not in snapshots
    destination = next(lot for lot in repository.position_requests.requests if lot.instrument_id == str(new))
    assert destination.open_event_trade_fill_id == str(opening.event_trade_fill_id)
    assert destination.open_event_corp_action_id == str(movement.event_corp_action_id)
    assert destination.opened_at_utc == opening.trade_timestamp_utc
    assert Decimal(destination.cost_basis_remaining) == 50


@pytest.mark.parametrize("basis", ["0", "80"])
def test_snapshot_distribution_uses_action_currency_and_is_absent_before_action_date(basis):
    from app.db import interfaces
    instrument = uuid4()
    movement = interfaces.LedgerSecurityMovementRecord(
        event_corp_action_id=uuid4(), source_raw_record_id=uuid4(), source_instrument_id=None,
        destination_instrument_id=instrument, report_date_local=date(2026, 8, 20), quantity="4",
        cost_basis=basis, currency="EUR",
    )
    rates = [LedgerFxRateRecord(currency="EUR", functional_currency="USD", fx_rate="2",
                                fx_source="test", ingestion_run_id=uuid4(), source_raw_record_id=uuid4(),
                                report_date_local=date(2026, 8, 20))]
    repository = _RepositoryStub(trades=[], valuations=[], fx_rates=rates)
    repository.db_ledger_security_movement_list_for_account = lambda **kwargs: [movement]
    service = _snapshot_service(repository)
    service.ledger_snapshot_build_and_persist("U_TEST", None, "2026-08-19", "USD")
    assert repository.snapshot_requests.requests == []
    service.ledger_snapshot_build_and_persist("U_TEST", None, "2026-08-20", "USD")
    snapshot = repository.snapshot_requests.requests[0]
    assert Decimal(snapshot.position_qty) == 4
    assert Decimal(snapshot.cost_basis) == Decimal(basis) * 2
    lot = repository.position_requests.requests[0]
    assert lot.open_event_trade_fill_id is None
    assert lot.open_event_corp_action_id == str(movement.event_corp_action_id)
    assert snapshot.provisional is True


def test_transferred_distribution_lots_keep_distinct_origins_after_round_trip():
    from app.db.interfaces import LedgerSecurityMovementRecord
    old, new = uuid4(), uuid4()
    first, second, outbound, returned = uuid4(), uuid4(), uuid4(), uuid4()
    movements = [LedgerSecurityMovementRecord(
        event_corp_action_id=event, source_raw_record_id=uuid4(), source_instrument_id=source,
        destination_instrument_id=destination, report_date_local=date(2026, 8, day),
        quantity=quantity, cost_basis=basis, currency="USD",
    ) for event, source, destination, day, quantity, basis in (
        (first, None, old, 18, "2", "20"), (second, None, old, 19, "3", "60"),
        (outbound, old, new, 20, "5", None), (returned, new, old, 21, "5", None),
    )]
    repository = _RepositoryStub(trades=[], valuations=[])
    repository.db_ledger_security_movement_list_for_account = lambda **kwargs: movements
    _snapshot_service(repository).ledger_snapshot_build_and_persist("U_TEST", None, "2026-08-21", "USD")
    lots = repository.position_requests.requests
    assert len(lots) == 6
    assert len({lot.position_lot_id for lot in lots}) == 6
    open_lots = [lot for lot in lots if lot.status == "open"]
    assert {lot.open_event_corp_action_id for lot in open_lots} == {str(first), str(second)}
    assert all(lot.open_event_trade_fill_id is None and lot.instrument_id == str(old) for lot in open_lots)
    assert sum(Decimal(lot.cost_basis_remaining) for lot in open_lots) == 80


def test_snapshot_output_filter_preserves_all_connected_lot_processing():
    from app.db.interfaces import LedgerSecurityMovementRecord
    old, new = uuid4(), uuid4()
    opening = replace(_trade(old, "BUY", "8", "10"), report_date_local=date(2026, 8, 19),
                      trade_timestamp_utc=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    movement = LedgerSecurityMovementRecord(
        event_corp_action_id=uuid4(), source_raw_record_id=uuid4(), source_instrument_id=old,
        destination_instrument_id=new, report_date_local=date(2026, 8, 20), quantity="8",
        cost_basis=None, currency="USD",
    )
    repository = _RepositoryStub(trades=[opening], valuations=[], scope_ids=[str(new)])
    repository.db_ledger_security_movement_list_for_account = lambda **kwargs: [movement]
    result = _snapshot_service(repository).ledger_snapshot_build_and_persist(
        "U_TEST", None, "2026-08-20", "USD", affected_conids=frozenset({"new"}),
        snapshot_instrument_ids=frozenset({str(new)}),
    )
    assert result.snapshot_row_count == 1
    assert [row.instrument_id for row in repository.snapshot_requests.requests] == [str(new)]
    assert {row.instrument_id for row in repository.position_requests.requests} == {str(old), str(new)}
    assert {row.status for row in repository.position_requests.requests} == {"open", "closed"}


@pytest.mark.parametrize("origin", ["trade", "distribution"])
@pytest.mark.parametrize("fx_rate", [None, "2"])
def test_same_day_transfer_chain_carries_transitive_fx_context_before_uuid_order(origin, fx_rate):
    from app.db.interfaces import LedgerSecurityMovementRecord
    old, middle, new = uuid4(), uuid4(), uuid4()
    origin_date = date(2026, 8, 19 if origin == "trade" else 20)
    opening = replace(_trade(old, "BUY", "8", "10"), currency="EUR", report_date_local=origin_date,
                      trade_timestamp_utc=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    sale = _trade(new, "SELL", "3", "30")
    movements = [LedgerSecurityMovementRecord(
        event_corp_action_id=UUID(int=event), source_raw_record_id=uuid4(), source_instrument_id=source,
        destination_instrument_id=destination, report_date_local=date(2026, 8, 20),
        quantity="8", cost_basis=None, currency="EUR",
    ) for event, source, destination in [(1, middle, new), (2, old, middle)]]
    if origin == "distribution":
        movements.append(replace(movements[-1], event_corp_action_id=UUID(int=3),
                                 source_instrument_id=None, destination_instrument_id=old, cost_basis="80"))
    rates = [] if fx_rate is None else [LedgerFxRateRecord(
        report_date_local=origin_date, currency="EUR", functional_currency="USD", fx_rate=fx_rate,
        fx_source="test", ingestion_run_id=uuid4(), source_raw_record_id=uuid4(),
    )]
    repository = _RepositoryStub(trades=[opening, sale] if origin == "trade" else [sale],
                                 valuations=[_broker_position(new, "5", mark="30")], fx_rates=rates)
    repository.db_ledger_security_movement_list_for_account = lambda **kwargs: movements
    _snapshot_service(repository).ledger_snapshot_build_and_persist("U_TEST", str(uuid4()), "2026-08-20", "USD")
    snapshot = next(row for row in repository.snapshot_requests.requests if row.instrument_id == str(new))
    assert snapshot.fx_dependencies == [{"currency": "EUR", "functional_currency": "USD",
                                         "date": origin_date.isoformat(), "rate": fx_rate}]
    assert snapshot.provisional is (fx_rate is None)
    assert ("FX_RATE_MISSING_ALL_SOURCES" if fx_rate is None else "conversion_rates_exact") in snapshot.fx_source
    assert Decimal(snapshot.position_qty) == 5
    assert Decimal(snapshot.cost_basis) == (0 if fx_rate is None else 100)
    assert Decimal(snapshot.realized_pnl) == (90 if fx_rate is None else 30)


def test_later_distribution_fx_context_does_not_flow_through_an_earlier_transfer():
    from app.db.interfaces import LedgerSecurityMovementRecord
    old, new = uuid4(), uuid4()
    opening = replace(_trade(old, "BUY", "8", "10"), report_date_local=date(2026, 8, 19),
                      trade_timestamp_utc=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    transfer = LedgerSecurityMovementRecord(
        event_corp_action_id=UUID(int=2), source_raw_record_id=uuid4(), source_instrument_id=old,
        destination_instrument_id=new, report_date_local=date(2026, 8, 20), quantity="8",
        cost_basis=None, currency="USD",
    )
    distribution = replace(transfer, event_corp_action_id=UUID(int=1), source_instrument_id=None,
                           destination_instrument_id=old, report_date_local=date(2026, 8, 21),
                           quantity="1", cost_basis="10", currency="EUR")
    repository = _RepositoryStub(trades=[opening], valuations=[_broker_position(new, "8")])
    repository.db_ledger_security_movement_list_for_account = lambda **kwargs: [distribution, transfer]
    _snapshot_service(repository).ledger_snapshot_build_and_persist("U_TEST", str(uuid4()), "2026-08-21", "USD")
    snapshot = next(row for row in repository.snapshot_requests.requests if row.instrument_id == str(new))
    assert snapshot.fx_dependencies == []
    assert snapshot.provisional is False
    assert Decimal(snapshot.cost_basis) == 80


@pytest.mark.parametrize("trade_day", [20, 21])
def test_source_trades_on_or_after_transfer_do_not_contaminate_destination_fx(trade_day):
    from app.db.interfaces import LedgerSecurityMovementRecord
    old, new = uuid4(), uuid4()
    opening = replace(_trade(old, "BUY", "8", "10"), report_date_local=date(2026, 8, 19),
                      trade_timestamp_utc=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    later = replace(_trade(old, "BUY", "1", "20"), currency="EUR", report_date_local=date(2026, 8, trade_day),
                    trade_timestamp_utc=datetime(2026, 8, trade_day, 12, tzinfo=timezone.utc))
    transfer = LedgerSecurityMovementRecord(
        event_corp_action_id=uuid4(), source_raw_record_id=uuid4(), source_instrument_id=old,
        destination_instrument_id=new, report_date_local=date(2026, 8, 20), quantity="8",
        cost_basis=None, currency="USD",
    )
    repository = _RepositoryStub(trades=[opening, later],
                                 valuations=[_broker_position(old, "1"), _broker_position(new, "8")])
    repository.db_ledger_security_movement_list_for_account = lambda **kwargs: [transfer]
    _snapshot_service(repository).ledger_snapshot_build_and_persist("U_TEST", str(uuid4()), "2026-08-21", "USD")
    snapshots = {row.instrument_id: row for row in repository.snapshot_requests.requests}
    assert snapshots[str(old)].provisional is True
    assert snapshots[str(old)].fx_dependencies == [{"currency": "EUR", "functional_currency": "USD",
                                                  "date": f"2026-08-{trade_day}", "rate": None}]
    destination = snapshots[str(new)]
    assert destination.provisional is False
    assert destination.fx_dependencies == []
    assert destination.fx_source == "base_currency"
    assert Decimal(destination.cost_basis) == 80
    assert Decimal(destination.unrealized_pnl) == 16


def test_transferred_lots_exclude_fx_from_source_lots_closed_before_transfer():
    from app.db.interfaces import LedgerSecurityMovementRecord
    old, new = uuid4(), uuid4()
    trades = [replace(_trade(old, side, quantity, price), currency=currency, report_date_local=date(2026, 8, day),
                      trade_timestamp_utc=datetime(2026, 8, day, 12, tzinfo=timezone.utc))
              for day, side, quantity, price, currency in [(17, "BUY", "1", "10", "EUR"),
                                                          (18, "SELL", "1", "20", "USD"),
                                                          (19, "BUY", "8", "10", "USD")]]
    transfer = LedgerSecurityMovementRecord(
        event_corp_action_id=uuid4(), source_raw_record_id=uuid4(), source_instrument_id=old,
        destination_instrument_id=new, report_date_local=date(2026, 8, 20), quantity="8",
        cost_basis=None, currency="USD",
    )
    repository = _RepositoryStub(trades=trades, valuations=[_broker_position(new, "8")])
    repository.db_ledger_security_movement_list_for_account = lambda **kwargs: [transfer]
    _snapshot_service(repository).ledger_snapshot_build_and_persist("U_TEST", str(uuid4()), "2026-08-20", "USD")
    snapshots = {row.instrument_id: row for row in repository.snapshot_requests.requests}
    assert snapshots[str(old)].provisional is True
    assert snapshots[str(new)].provisional is False
    assert snapshots[str(new)].fx_dependencies == []
    assert Decimal(snapshots[str(new)].cost_basis) == 80


@pytest.mark.parametrize("fx_rate", [None, "2"])
def test_split_rounding_keeps_donor_fx_context_when_surviving_lot_transfers(fx_rate):
    from app.db.interfaces import LedgerSecurityMovementRecord
    old, new = uuid4(), uuid4()
    donor = replace(_trade(old, "BUY", "0.00000001", "100000000"), currency="EUR",
                    report_date_local=date(2026, 8, 18), trade_timestamp_utc=datetime(2026, 8, 18, 12, tzinfo=timezone.utc))
    survivor = replace(_trade(old, "BUY", "1", "10"), report_date_local=date(2026, 8, 19),
                       trade_timestamp_utc=datetime(2026, 8, 19, 12, tzinfo=timezone.utc))
    transfer = LedgerSecurityMovementRecord(
        event_corp_action_id=uuid4(), source_raw_record_id=uuid4(), source_instrument_id=old,
        destination_instrument_id=new, report_date_local=date(2026, 8, 20), quantity="0.1",
        cost_basis=None, currency="USD",
    )
    rates = [] if fx_rate is None else [LedgerFxRateRecord(
        report_date_local=date(2026, 8, 18), currency="EUR", functional_currency="USD", fx_rate=fx_rate,
        fx_source="test", ingestion_run_id=uuid4(), source_raw_record_id=uuid4(),
    )]
    repository = _RepositoryStub(
        trades=[donor, survivor], valuations=[_broker_position(new, "0.1", mark="120")], fx_rates=rates,
        corporate_actions=[LedgerCorporateActionRecord(old, date(2026, 8, 20), "REVERSESPLIT", "0.1")],
    )
    repository.db_ledger_security_movement_list_for_account = lambda **kwargs: [transfer]
    _snapshot_service(repository).ledger_snapshot_build_and_persist("U_TEST", str(uuid4()), "2026-08-20", "USD")
    snapshot = next(row for row in repository.snapshot_requests.requests if row.instrument_id == str(new))
    assert snapshot.fx_dependencies == [{"currency": "EUR", "functional_currency": "USD",
                                         "date": "2026-08-18", "rate": fx_rate}]
    assert snapshot.provisional is (fx_rate is None)
    assert Decimal(snapshot.cost_basis) == (10 if fx_rate is None else 12)
