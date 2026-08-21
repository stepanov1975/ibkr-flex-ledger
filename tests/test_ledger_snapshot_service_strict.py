"""Regression tests for strict solid-valuation snapshot behavior."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.db.interfaces import (
    LedgerCashflowRecord,
    LedgerCorporateActionRecord,
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
        corporate_actions: list[LedgerCorporateActionRecord] | None = None,
        scope_ids: list[str] | None = None,
        instrument_currencies: list[str] | None = None,
    ) -> None:
        self._trades = trades
        self._valuations = valuations
        self._fx_rates = fx_rates or []
        self._cashflows = cashflows or []
        self._corporate_actions = corporate_actions or []
        self._scope_ids = list(scope_ids or [])
        self._instrument_currencies = list(instrument_currencies or [])
        self.position_requests = _SnapshotCapture(requests=[])
        self.snapshot_requests = _SnapshotCapture(requests=[])
        self.reconcile_call_count = 0
        self.trade_instrument_ids = None
        self.cashflow_instrument_ids = None
        self.corp_action_instrument_ids = None
        self.open_position_instrument_ids = None
        self.reconciled_instrument_ids = None
        self.fx_currencies = None
        self.read_call_count = 0

    def db_ledger_instrument_ids_for_scope(self, account_id: str, conids: tuple[str, ...], currencies: tuple[str, ...]):
        """Return the configured deterministic instrument scope."""
        _ = (account_id, conids, currencies)
        self.read_call_count += 1
        return self._scope_ids

    def db_ledger_instrument_currency_list(self, instrument_ids: tuple[str, ...]):
        """Return currencies for the configured instrument scope."""
        _ = instrument_ids
        self.read_call_count += 1
        return self._instrument_currencies

    def db_ledger_trade_fill_list_for_account(
        self,
        account_id: str,
        through_report_date_local: str | None = None,
        instrument_ids: tuple[str, ...] | None = None,
    ):
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
    ):
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
    ):
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
    ):
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
    ):
        """Return deterministic corporate-action adjustments."""
        _ = (account_id, through_report_date_local)
        self.read_call_count += 1
        self.corp_action_instrument_ids = instrument_ids
        return self._corporate_actions

    def db_position_lot_upsert_many(self, requests):
        """Capture position-lot upsert payload for assertions."""
        self.position_requests.requests = requests

    def db_position_lot_reconcile_open(
        self,
        account_id: str,
        closed_at_utc: datetime,
        requests,
        instrument_ids: tuple[str, ...] | None = None,
    ):
        """Capture the reconciled open-lot projection."""
        _ = (account_id, closed_at_utc)
        self.reconciled_instrument_ids = instrument_ids
        self.reconcile_call_count += 1
        self.position_requests.requests = requests

    def db_pnl_snapshot_daily_upsert_many(self, requests):
        """Capture snapshot upsert payload for assertions."""
        self.snapshot_requests.requests = requests


def _trade(instrument_id: UUID, side: str, quantity: str, price: str) -> LedgerTradeFillRecord:
    return LedgerTradeFillRecord(
        event_trade_fill_id=uuid4(),
        account_id="U_TEST",
        instrument_id=instrument_id,
        source_raw_record_id=uuid4(),
        trade_timestamp_utc=datetime(2026, 8, 20, 10, tzinfo=timezone.utc),
        report_date_local=date(2026, 8, 20),
        side=side,
        quantity=quantity,
        price=price,
        fees="0",
        commission="0",
        functional_currency="USD",
        currency="USD",
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
    service = StockLedgerSnapshotService(repository=repository)

    result = service.ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=None,
        report_date_local="2026-02-20",
        functional_currency="USD",
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
        asset_category="STK",
        currency="USD",
        position_qty="10",
        mark_price="120",
        cost_basis_money=None,
        broker_unrealized_pnl="200",
        fx_rate_to_base=None,
        multiplier=None,
        report_date_local=date(2026, 2, 20),
    )
    repository = _RepositoryStub(trades=[trade], valuations=[valuation])
    service = StockLedgerSnapshotService(repository=repository)

    result = service.ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=str(uuid4()),
        report_date_local="2026-02-20",
        functional_currency="USD",
    )

    assert result.missing_solid_valuation_count == 0
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.provisional is False
    assert snapshot.valuation_source == "openpositions_unrealized_pnl"
    assert Decimal(snapshot.unrealized_pnl) == Decimal("200")


def test_snapshot_converts_foreign_trade_and_broker_unrealized_to_base_currency() -> None:
    """Apply trade and broker FX before emitting USD-valued snapshot amounts."""

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
        multiplier=None,
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
        functional_currency="USD",
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.currency == "USD"
    assert snapshot.provisional is False
    assert Decimal(snapshot.cost_basis) == Decimal("1201.2")
    assert Decimal(snapshot.unrealized_pnl) == Decimal("1198.8")
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
        cost_basis_money=None, broker_unrealized_pnl="200", fx_rate_to_base=None, multiplier=None,
        report_date_local=date(2026, 2, 20),
    )
    repository = _RepositoryStub(trades=[trade], valuations=[valuation], corporate_actions=[action])

    StockLedgerSnapshotService(repository=repository).ledger_snapshot_build_and_persist(
        account_id="U_TEST",
        ingestion_run_id=str(uuid4()),
        report_date_local="2026-02-20",
        functional_currency="USD",
    )

    snapshot = repository.snapshot_requests.requests[0]
    assert Decimal(snapshot.position_qty) == Decimal("20")
    assert Decimal(snapshot.cost_basis) == Decimal("1000")
    assert Decimal(snapshot.unrealized_pnl) == Decimal("200")


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
        functional_currency="USD",
    )

    assert repository.reconcile_call_count == 1
    assert repository.position_requests.requests == []


def test_snapshot_build_limits_reads_and_writes_to_resolved_scope() -> None:
    """Propagate one resolved instrument scope through every scoped read and write."""

    instrument_id = "00000000-0000-0000-0000-000000000010"
    trade = LedgerTradeFillRecord(
        event_trade_fill_id=uuid4(),
        account_id="U1",
        instrument_id=instrument_id,
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
        instrument_id="00000000-0000-0000-0000-000000000099",
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
        instrument_id=instrument_id,
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

    StockLedgerSnapshotService(repository=repository).ledger_snapshot_build_and_persist(
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
    assert repository.reconciled_instrument_ids == expected
    assert repository.fx_currencies == ("AUD", "CHF", "EUR", "GBP", "JPY", "USD")
    assert [request.instrument_id for request in repository.position_requests.requests] == [instrument_id]
    assert [request.instrument_id for request in repository.snapshot_requests.requests] == [instrument_id]


def test_snapshot_build_empty_scope_is_noop() -> None:
    """Avoid every repository read when both affected scope sets are empty."""

    repository = _RepositoryStub(trades=[], valuations=[])

    result = StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U1",
        "00000000-0000-0000-0000-000000000001",
        "2026-08-21",
        "USD",
        frozenset(),
        frozenset(),
    )

    assert result.snapshot_row_count == 0
    assert result.position_lot_row_count == 0
    assert repository.read_call_count == 0
    assert repository.reconcile_call_count == 0


def test_snapshot_build_none_scope_retains_full_reads() -> None:
    """Keep the existing full-history read and reconciliation contract by default."""

    repository = _RepositoryStub(trades=[], valuations=[])

    StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
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
    result = StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "0"
    assert snapshot.cost_basis is None
    assert snapshot.unrealized_pnl == "0"
    assert snapshot.provisional is True
    assert result.broker_position_mismatch_count == 1


def test_snapshot_treats_broker_absence_as_zero_without_synthetic_lot() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(instrument_id, "SELL", "200", "11")],
        valuations=[],
    )
    result = StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    assert repository.snapshot_requests.requests[0].position_qty == "0"
    assert repository.snapshot_requests.requests[0].provisional is True
    assert len(repository.position_requests.requests) == 1
    assert result.broker_absent_nonzero_fifo_count == 1


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
    result = StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
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


def test_snapshot_exact_match_keeps_fifo_cost_and_uses_broker_unrealized() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(instrument_id, "BUY", "10", "10")],
        valuations=[_broker_position(
            instrument_id, "10", mark="12", cost="100", unrealized="200"
        )],
    )
    result = StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "10"
    assert snapshot.cost_basis == "100"
    assert snapshot.unrealized_pnl == "200"
    assert snapshot.provisional is False
    assert result.broker_position_match_count == 1


def test_snapshot_preserves_missing_optional_broker_values() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[],
        valuations=[_broker_position(
            instrument_id, "5", mark=None, cost=None, unrealized=None
        )],
    )
    StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
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
    StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
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
    result = StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    snapshot = repository.snapshot_requests.requests[0]
    assert snapshot.position_qty == "0"
    assert snapshot.realized_pnl == "25"
    assert result.broker_position_match_count == 0
    assert result.broker_position_mismatch_count == 0
    assert result.broker_only_position_count == 0
    assert result.broker_absent_nonzero_fifo_count == 0


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
    StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    assert repository.snapshot_requests.requests[0].unrealized_pnl == "-193.00"


def test_snapshot_exact_short_match_keeps_signed_fifo_cost() -> None:
    instrument_id = uuid4()
    repository = _RepositoryStub(
        trades=[_trade(instrument_id, "SELL", "2", "11")],
        valuations=[_broker_position(instrument_id, "-2", cost="-20", unrealized="-2")],
    )
    StockLedgerSnapshotService(repository).ledger_snapshot_build_and_persist(
        "U_TEST", str(uuid4()), "2026-08-20", "USD"
    )
    assert repository.snapshot_requests.requests[0].cost_basis == "-22"
