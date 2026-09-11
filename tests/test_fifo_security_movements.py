"""Accounting regressions for dated security transfers and distributions."""
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.ledger import fifo_engine as fifo


def _trade(day, side, quantity, price, identifier):
    return fifo.FifoTradeFillInput(
        event_trade_fill_id=identifier, source_raw_record_id=identifier,
        trade_timestamp_utc=f"2026-08-{day:02}T12:00:00+00:00", report_date_local=date(2026, 8, day),
        side=side, quantity=Decimal(quantity), price=Decimal(price), fees=None, withholding_tax=None,
    )


def _request(instrument, trades):
    return fifo.FifoLedgerComputationRequest("account", instrument, "USD", Decimal(30), trades)


def _movement(source="old", quantity="8", basis=None, day=20, event="action"):
    return fifo.FifoSecurityMovementInput(
        event_corp_action_id=event, source_raw_record_id=event, source_instrument_id=source,
        destination_instrument_id="new", report_date_local=date(2026, 8, day),
        quantity=Decimal(quantity), cost_basis=Decimal(basis) if basis is not None else None,
    )


def test_transfer_preserves_surviving_fifo_and_keeps_realized_with_source():
    old = _request("old", [_trade(10, "BUY", "5", "10", "buy1"),
                           _trade(11, "BUY", "5", "20", "buy2"),
                           _trade(15, "SELL", "2", "30", "old-sale")])
    new = _request("new", [_trade(21, "SELL", "4", "30", "new-sale")])
    results = fifo.fifo_compute_portfolio([new, old], (_movement(),))
    source, destination = results["old"], results["new"]
    assert source.position_quantity == 0
    assert source.realized_pnl == 40
    assert sum(lot.realized_pnl_to_date for lot in source.closed_lots) == 40
    assert all(lot.closed_report_date_local == date(2026, 8, 20) for lot in source.closed_lots)
    assert destination.position_quantity == 4
    assert destination.realized_pnl == 70
    assert destination.open_lots[0].cost_basis_remaining == 80
    assert destination.open_lots[0].opened_at_utc == old.trades[1].trade_timestamp_utc
    assert destination.open_lots[0].open_event_trade_fill_id == "buy2"
    assert destination.open_lots[0].open_event_corp_action_id == "action"
    assert sum(lot.realized_pnl_to_date for lot in (*destination.open_lots, *destination.closed_lots)) == 70


def test_transfer_is_effective_before_same_day_trades_and_after_same_day_split():
    old = replace(_request("old", [_trade(10, "BUY", "4", "20", "buy")]),
                  splits=(fifo.FifoSplitInput(date(2026, 8, 20), Decimal(2)),))
    new = _request("new", [_trade(20, "SELL", "3", "30", "sale")])
    results = fifo.fifo_compute_portfolio([old, new], (_movement(),))
    assert results["new"].position_quantity == 5
    assert results["new"].realized_pnl == 60
    assert results["new"].open_lots[0].cost_basis_remaining == 50


@pytest.mark.parametrize("basis,realized,remaining_basis", [("0", "30", "0"), ("80", "10", "60")])
def test_distribution_opens_real_corporate_action_lot_with_explicit_basis(basis, realized, remaining_basis):
    request = _request("new", [_trade(21, "SELL", "1", "30", "sale")])
    result = fifo.fifo_compute_portfolio([request], (_movement(None, "4", basis),))["new"]
    assert result.position_quantity == 3
    assert result.realized_pnl == Decimal(realized)
    lot = result.open_lots[0]
    assert lot.open_event_trade_fill_id is None
    assert lot.open_event_corp_action_id == "action"
    assert lot.cost_basis_remaining == Decimal(remaining_basis)
    assert lot.opened_at_utc == "2026-08-19T21:00:00+00:00"


@pytest.mark.parametrize("side,quantity", [("BUY", "7"), ("BUY", "9"), ("SELL", "8")])
def test_transfer_rejects_partial_or_short_source_position(side, quantity):
    old = _request("old", [_trade(10, side, quantity, "10", "buy")])
    with pytest.raises(ValueError, match="full long position"):
        fifo.fifo_compute_portfolio([old, _request("new", [])], (_movement(),))


@pytest.mark.parametrize("basis", [None, "-1", "NaN", "Infinity"])
def test_distribution_rejects_unspecified_or_invalid_basis(basis):
    with pytest.raises(ValueError, match="explicit nonnegative finite"):
        fifo.fifo_compute_portfolio([_request("new", [])], (_movement(None, "4", basis),))


def test_transfer_rejects_destination_short_and_entered_transfer_basis():
    requests = [_request("old", [_trade(10, "BUY", "8", "10", "buy")]),
                _request("new", [_trade(11, "SELL", "1", "10", "short")])]
    with pytest.raises(ValueError, match="destination short"):
        fifo.fifo_compute_portfolio(requests, (_movement(),))
    with pytest.raises(ValueError, match="carry existing basis"):
        fifo.fifo_compute_portfolio(requests, (_movement(basis="80"),))


def test_multiple_transfers_preserve_source_history_and_preexisting_destination_fifo_order():
    requests = [_request("old", [_trade(10, "BUY", "8", "10", "buy-old")]),
                _request("new", [_trade(12, "BUY", "2", "20", "buy-new"),
                                 _trade(21, "SELL", "3", "30", "sale-new")]),
                _request("third", [_trade(23, "SELL", "6", "40", "sale-third")])]
    second = replace(_movement("new", "7", day=22, event="second"), destination_instrument_id="third")
    results = fifo.fifo_compute_portfolio(requests, (_movement(), second))
    assert results["new"].realized_pnl == 60  # Acquired earlier: transferred old lot closes first.
    assert results["third"].realized_pnl == 170
    assert results["third"].open_lots[0].cost_basis_remaining == 20
    assert results["third"].open_lots[0].open_event_trade_fill_id == "buy-new"


@pytest.mark.parametrize("reverse_input", [False, True])
def test_same_day_transfer_chain_uses_dependencies_before_event_uuid(reverse_input):
    old = replace(_request("old", [_trade(10, "BUY", "4", "20", "buy")]),
                  splits=(fifo.FifoSplitInput(date(2026, 8, 20), Decimal(2)),))
    first = _movement(event="ffffffff-ffff-ffff-ffff-ffffffffffff")
    second = replace(_movement("new", event="00000000-0000-0000-0000-000000000001"),
                     destination_instrument_id="third")
    movements = (second, first) if reverse_input else (first, second)
    results = fifo.fifo_compute_portfolio(
        [old, _request("new", []), _request("third", [_trade(20, "SELL", "3", "30", "sale")])],
        movements,
    )
    assert results["old"].position_quantity == results["new"].position_quantity == 0
    assert results["new"].closed_lots[0].closed_report_date_local == date(2026, 8, 20)
    destination = results["third"]
    assert destination.position_quantity == 5
    assert destination.realized_pnl == 60
    lot = destination.open_lots[0]
    assert lot.cost_basis_remaining == 50
    assert lot.opened_at_utc == old.trades[0].trade_timestamp_utc
    assert lot.open_event_trade_fill_id == "buy"
    assert lot.open_event_corp_action_id == second.event_corp_action_id
    assert lot.transfer_event_corp_action_id == second.event_corp_action_id


def test_same_day_distribution_precedes_its_transfer_and_preserves_distribution_lineage():
    distribution = replace(_movement(None, "8", "80", event="z-distribution"),
                           destination_instrument_id="old")
    transfer = _movement(event="a-transfer")
    results = fifo.fifo_compute_portfolio(
        [_request("old", []), _request("new", [_trade(21, "SELL", "3", "30", "sale")])],
        (transfer, distribution),
    )
    assert results["old"].position_quantity == 0
    assert results["new"].position_quantity == 5
    assert results["new"].realized_pnl == 60
    lot = results["new"].open_lots[0]
    assert lot.cost_basis_remaining == 50
    assert lot.open_event_trade_fill_id is None
    assert lot.open_event_corp_action_id == "z-distribution"
    assert lot.transfer_event_corp_action_id == "a-transfer"


@pytest.mark.parametrize("reverse_input", [False, True])
def test_same_day_independent_transfers_keep_separate_cost_basis(reverse_input):
    requests = [_request("old", [_trade(10, "BUY", "8", "10", "buy-old")]),
                _request("new", []),
                _request("other", [_trade(11, "BUY", "4", "30", "buy-other")]),
                _request("fourth", [])]
    first = _movement(event="z-transfer")
    second = replace(_movement("other", "4", event="a-transfer"), destination_instrument_id="fourth")
    movements = (second, first) if reverse_input else (first, second)
    results = fifo.fifo_compute_portfolio(requests, movements)
    assert results["old"].position_quantity == results["other"].position_quantity == 0
    assert results["new"].position_quantity == 8
    assert results["fourth"].position_quantity == 4
    assert results["new"].open_lots[0].cost_basis_remaining == 80
    assert results["fourth"].open_lots[0].cost_basis_remaining == 120


def test_same_day_transfer_cycle_is_rejected_with_action_identity():
    first = _movement(event="first")
    second = replace(_movement("new", event="second"), destination_instrument_id="old")
    requests = [_request("old", [_trade(10, "BUY", "8", "10", "buy")]), _request("new", [])]
    with pytest.raises(fifo.FifoSecurityMovementError, match="cyclic") as error:
        fifo.fifo_compute_portfolio(requests, (second, first))
    assert error.value.event_corp_action_id in {"first", "second"}


def test_same_day_competing_transfers_are_rejected_as_ambiguous():
    first = _movement(event="first")
    second = replace(_movement(event="second"), destination_instrument_id="third")
    requests = [_request("old", [_trade(10, "BUY", "8", "10", "buy")]),
                _request("new", []), _request("third", [])]
    with pytest.raises(fifo.FifoSecurityMovementError, match="ambiguous") as error:
        fifo.fifo_compute_portfolio(requests, (second, first))
    assert error.value.event_corp_action_id in {"first", "second"}


def test_empty_movements_preserve_existing_fifo_results():
    request = _request("old", [_trade(10, "BUY", "8", "10", "buy")])
    assert fifo.fifo_compute_portfolio([request], ()) == {"old": fifo.fifo_compute_instrument(request)}


def test_transfer_preserves_numeric_transaction_order_for_tied_acquisitions():
    earlier = replace(_trade(10, "BUY", "1", "10", "z"), transaction_id="2")
    later = replace(_trade(10, "BUY", "1", "20", "a"), transaction_id="10")
    results = fifo.fifo_compute_portfolio(
        [_request("old", [later, earlier]), _request("new", [_trade(21, "SELL", "1", "30", "sale")])],
        (_movement(quantity="2"),),
    )
    assert results["new"].realized_pnl == 20
    assert results["new"].open_lots[0].open_event_trade_fill_id == "a"


def test_unrelated_distribution_does_not_reorder_other_instruments_execution_history():
    trades = [replace(_trade(18, "BUY", "1", "100", "first"), report_date_local=date(2026, 8, 20)),
              _trade(19, "BUY", "1", "200", "second"),
              _trade(20, "SELL", "1", "300", "sale")]
    unrelated = _request("unrelated", trades)
    results = fifo.fifo_compute_portfolio([unrelated, _request("new", [])], (_movement(None, "1", "0"),))
    assert results["unrelated"] == fifo.fifo_compute_instrument(unrelated)


def test_invalid_transfer_identifies_the_saved_action_to_reopen():
    requests = [_request("old", [_trade(10, "BUY", "7", "10", "buy")]), _request("new", [])]
    with pytest.raises(ValueError) as error:
        fifo.fifo_compute_portfolio(requests, (_movement(event="specific-action"),))
    assert isinstance(error.value, fifo.FifoSecurityMovementError)
    assert error.value.event_corp_action_id == "specific-action"
