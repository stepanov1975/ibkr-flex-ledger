"""Split precision, ordering, and basis preservation at FIFO boundaries."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.ledger.fifo_engine import FifoLedgerComputationRequest, FifoSplitInput, FifoTradeFillInput, fifo_compute_instrument


def _trade(index, quantity, price, side="BUY", report_day=20, execution_day=20):
    return FifoTradeFillInput(
        source_raw_record_id=str(index), trade_timestamp_utc=f"2026-08-{execution_day:02d}T12:00:{index:02d}+00:00",
        report_date_local=date(2026, 8, report_day), side=side, quantity=Decimal(quantity),
        price=Decimal(price), fees=Decimal("1"), withholding_tax=None,
    )


def _request(trades, splits=()):
    return FifoLedgerComputationRequest(
        account_id="TEST", instrument_id="TEST", functional_currency="USD", mark_price=Decimal("200"),
        trades=trades, splits=splits,
    )


@pytest.mark.parametrize("tiny_index", [0, 1, 2])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_zero_lot_allocation_retains_total_basis_and_full_close(tiny_index, side):
    trades = [_trade(index, "50", "10", side) for index in range(3)]
    trades[tiny_index] = _trade(tiny_index, "0.00000001", "100000000", side)
    original = fifo_compute_instrument(_request(trades))
    splits = (FifoSplitInput(date(2026, 8, 21), Decimal("0.1")),)
    result = fifo_compute_instrument(_request(trades, splits))
    assert abs(result.position_quantity) == 10
    before_basis = sum(lot.cost_basis_open / lot.open_quantity * lot.remaining_quantity for lot in original.open_lots)
    after_basis = sum(lot.cost_basis_open / lot.open_quantity * lot.remaining_quantity for lot in result.open_lots)
    assert after_basis.quantize(Decimal("0.00000001")) == before_basis
    closing = _trade(4, "10", "200", "SELL" if side == "BUY" else "BUY", report_day=21, execution_day=21)
    closed = fifo_compute_instrument(_request([*trades, closing], splits))
    assert closed.open_lots == ()
    expected = (2000 - before_basis if side == "BUY" else -before_basis - 2000) - 1
    assert closed.realized_pnl.quantize(Decimal("0.00000001")) == expected


def test_split_rejects_only_unrepresentable_aggregate_position():
    request = _request([_trade(0, "0.00000001", "100")], (FifoSplitInput(date(2026, 8, 21), Decimal("0.1")),))
    with pytest.raises(ValueError, match="position below"):
        fifo_compute_instrument(request)


def test_same_date_split_factors_are_combined_before_rounding():
    splits = (FifoSplitInput(date(2026, 8, 21), Decimal("2")), FifoSplitInput(date(2026, 8, 21), Decimal("1.5")))
    request = _request([_trade(0, "0.00000001", "100000000")], splits)
    forward = fifo_compute_instrument(request)
    reverse = fifo_compute_instrument(replace(request, splits=tuple(reversed(splits))))
    assert forward == reverse
    assert forward.position_quantity == Decimal("0.00000003")


@pytest.mark.parametrize("split_day", [17, 21])
def test_execution_order_is_preserved_within_each_split_interval(split_day):
    trades = [
        _trade(0, "1", "100", report_day=20, execution_day=18),
        _trade(1, "1", "200", report_day=19, execution_day=19),
        _trade(2, "1", "300", "SELL", report_day=20, execution_day=20),
    ]
    before = fifo_compute_instrument(_request(trades))
    after = fifo_compute_instrument(_request(trades, (FifoSplitInput(date(2026, 8, split_day), Decimal("2")),)))
    assert before.realized_pnl == 198
    assert after.realized_pnl == before.realized_pnl


def test_zero_remaining_allocation_does_not_move_completed_lot_realizations():
    trades = [
        _trade(0, "1.00000001", "100"),
        _trade(1, "100", "100"),
        _trade(2, "1", "200", "SELL"),
    ]
    before = fifo_compute_instrument(_request(trades))
    assert before.open_lots[0].realized_pnl_to_date > 0
    assert before.open_lots[1].realized_pnl_to_date == 0
    after = fifo_compute_instrument(_request(trades, (FifoSplitInput(date(2026, 8, 21), Decimal("0.1")),)))
    assert after.realized_pnl == before.realized_pnl
    assert len(after.open_lots) == 1
    assert after.open_lots[0].open_event_trade_fill_id == before.open_lots[1].open_event_trade_fill_id
    assert after.open_lots[0].realized_pnl_to_date == 0
