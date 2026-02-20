"""Regression tests for Task 7 FIFO ledger and daily snapshot determinism."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.ledger.fifo_engine import FifoLedgerComputationRequest, FifoTradeFillInput, fifo_compute_instrument
from app.ledger.snapshot_dates import snapshot_resolve_report_date_local


def test_ledger_fifo_partial_close_includes_trade_fees_in_realized_and_unrealized_pnl() -> None:
    """Compute deterministic FIFO PnL for partial close with fee-adjusted cost basis.

    Returns:
        None: Assertions validate FIFO realized/unrealized totals.

    Raises:
        AssertionError: Raised when FIFO computation deviates from expected totals.
    """

    request = FifoLedgerComputationRequest(
        account_id="U_TEST",
        instrument_id="instrument-1",
        functional_currency="USD",
        mark_price=Decimal("130"),
        trades=[
            FifoTradeFillInput(
                source_raw_record_id="00000000-0000-0000-0000-000000000001",
                trade_timestamp_utc="2026-01-02T10:00:00+00:00",
                side="BUY",
                quantity=Decimal("10"),
                price=Decimal("100"),
                fees=Decimal("1.0"),
                withholding_tax=Decimal("0"),
            ),
            FifoTradeFillInput(
                source_raw_record_id="00000000-0000-0000-0000-000000000002",
                trade_timestamp_utc="2026-01-03T10:00:00+00:00",
                side="SELL",
                quantity=Decimal("4"),
                price=Decimal("120"),
                fees=Decimal("1.0"),
                withholding_tax=Decimal("0"),
            ),
        ],
    )

    result = fifo_compute_instrument(request)

    assert result.position_quantity == Decimal("6")
    assert result.realized_pnl == Decimal("78.6")
    assert result.unrealized_pnl == Decimal("179.4")


def test_ledger_fifo_output_is_deterministic_for_same_timestamp_order_ties() -> None:
    """Preserve deterministic FIFO outputs when input rows arrive in different order.

    Returns:
        None: Assertions validate replay-stable deterministic outputs.

    Raises:
        AssertionError: Raised when shuffled input changes computed totals.
    """

    tied_trades = [
        FifoTradeFillInput(
            source_raw_record_id="00000000-0000-0000-0000-000000000010",
            trade_timestamp_utc="2026-02-11T12:00:00+00:00",
            side="BUY",
            quantity=Decimal("2"),
            price=Decimal("50"),
            fees=Decimal("0.2"),
            withholding_tax=Decimal("0"),
        ),
        FifoTradeFillInput(
            source_raw_record_id="00000000-0000-0000-0000-000000000009",
            trade_timestamp_utc="2026-02-11T12:00:00+00:00",
            side="BUY",
            quantity=Decimal("3"),
            price=Decimal("70"),
            fees=Decimal("0.3"),
            withholding_tax=Decimal("0"),
        ),
        FifoTradeFillInput(
            source_raw_record_id="00000000-0000-0000-0000-000000000011",
            trade_timestamp_utc="2026-02-12T12:00:00+00:00",
            side="SELL",
            quantity=Decimal("4"),
            price=Decimal("80"),
            fees=Decimal("0.4"),
            withholding_tax=Decimal("0"),
        ),
    ]

    forward_request = FifoLedgerComputationRequest(
        account_id="U_TEST",
        instrument_id="instrument-2",
        functional_currency="USD",
        mark_price=Decimal("75"),
        trades=tied_trades,
    )
    reverse_request = FifoLedgerComputationRequest(
        account_id="U_TEST",
        instrument_id="instrument-2",
        functional_currency="USD",
        mark_price=Decimal("75"),
        trades=list(reversed(tied_trades)),
    )

    forward_result = fifo_compute_instrument(forward_request)
    reverse_result = fifo_compute_instrument(reverse_request)

    assert reverse_result == forward_result


def test_ledger_fifo_ignores_zero_quantity_rows() -> None:
    """Skip zero-quantity trades so non-impact broker rows do not fail snapshots.

    Returns:
        None: Assertions validate zero-quantity handling behavior.

    Raises:
        AssertionError: Raised when zero-quantity row changes result semantics.
    """

    request = FifoLedgerComputationRequest(
        account_id="U_TEST",
        instrument_id="instrument-3",
        functional_currency="USD",
        mark_price=Decimal("110"),
        trades=[
            FifoTradeFillInput(
                source_raw_record_id="00000000-0000-0000-0000-000000000100",
                trade_timestamp_utc="2026-02-10T10:00:00+00:00",
                side="BUY",
                quantity=Decimal("10"),
                price=Decimal("100"),
                fees=Decimal("0"),
                withholding_tax=Decimal("0"),
            ),
            FifoTradeFillInput(
                source_raw_record_id="00000000-0000-0000-0000-000000000101",
                trade_timestamp_utc="2026-02-10T11:00:00+00:00",
                side="BUY",
                quantity=Decimal("0"),
                price=Decimal("999"),
                fees=Decimal("0"),
                withholding_tax=Decimal("0"),
            ),
            FifoTradeFillInput(
                source_raw_record_id="00000000-0000-0000-0000-000000000102",
                trade_timestamp_utc="2026-02-10T12:00:00+00:00",
                side="SELL",
                quantity=Decimal("4"),
                price=Decimal("120"),
                fees=Decimal("0"),
                withholding_tax=Decimal("0"),
            ),
        ],
    )

    result = fifo_compute_instrument(request)

    assert result.position_quantity == Decimal("6")
    assert result.realized_pnl == Decimal("80")
    assert result.unrealized_pnl == Decimal("60")


def test_ledger_fifo_handles_signed_sell_quantity_inputs() -> None:
    """Normalize signed broker quantities and use side as the trade direction source.

    Returns:
        None: Assertions validate signed-quantity normalization.

    Raises:
        AssertionError: Raised when signed SELL quantities fail or miscompute PnL.
    """

    request = FifoLedgerComputationRequest(
        account_id="U_TEST",
        instrument_id="instrument-4",
        functional_currency="USD",
        mark_price=Decimal("130"),
        trades=[
            FifoTradeFillInput(
                source_raw_record_id="00000000-0000-0000-0000-000000000201",
                trade_timestamp_utc="2026-01-02T10:00:00+00:00",
                side="BUY",
                quantity=Decimal("10"),
                price=Decimal("100"),
                fees=Decimal("0"),
                withholding_tax=Decimal("0"),
            ),
            FifoTradeFillInput(
                source_raw_record_id="00000000-0000-0000-0000-000000000202",
                trade_timestamp_utc="2026-01-03T10:00:00+00:00",
                side="SELL",
                quantity=Decimal("-4"),
                price=Decimal("120"),
                fees=Decimal("0"),
                withholding_tax=Decimal("0"),
            ),
        ],
    )

    result = fifo_compute_instrument(request)

    assert result.position_quantity == Decimal("6")
    assert result.realized_pnl == Decimal("80")
    assert result.unrealized_pnl == Decimal("180")


def test_ledger_fifo_supports_short_open_and_partial_cover() -> None:
    """Support short inventory flows where sells can open short lots and buys can cover them.

    Returns:
        None: Assertions validate short-lot FIFO behavior.

    Raises:
        AssertionError: Raised when short-lot matching or PnL is incorrect.
    """

    request = FifoLedgerComputationRequest(
        account_id="U_TEST",
        instrument_id="instrument-5",
        functional_currency="USD",
        mark_price=Decimal("80"),
        trades=[
            FifoTradeFillInput(
                source_raw_record_id="00000000-0000-0000-0000-000000000301",
                trade_timestamp_utc="2026-01-02T10:00:00+00:00",
                side="SELL",
                quantity=Decimal("-5"),
                price=Decimal("100"),
                fees=Decimal("0"),
                withholding_tax=Decimal("0"),
            ),
            FifoTradeFillInput(
                source_raw_record_id="00000000-0000-0000-0000-000000000302",
                trade_timestamp_utc="2026-01-03T10:00:00+00:00",
                side="BUY",
                quantity=Decimal("2"),
                price=Decimal("90"),
                fees=Decimal("0"),
                withholding_tax=Decimal("0"),
            ),
        ],
    )

    result = fifo_compute_instrument(request)

    assert result.position_quantity == Decimal("-3")
    assert result.realized_pnl == Decimal("20")
    assert result.unrealized_pnl == Decimal("60")
    assert len(result.open_lots) == 1
    assert result.open_lots[0].remaining_quantity == Decimal("-3")


def test_snapshot_report_date_uses_asia_jerusalem_timezone_across_dst_edges() -> None:
    """Resolve report date in Asia/Jerusalem from UTC timestamps for DST-edge instants.

    Returns:
        None: Assertions validate timezone conversion contract.

    Raises:
        AssertionError: Raised when report date does not match timezone-aware conversion.
    """

    utc_timestamps = [
        "2026-03-27T00:30:00+00:00",
        "2026-10-25T00:30:00+00:00",
    ]

    for utc_timestamp in utc_timestamps:
        expected_local_date = (
            datetime.fromisoformat(utc_timestamp)
            .astimezone(ZoneInfo("Asia/Jerusalem"))
            .date()
            .isoformat()
        )
        assert snapshot_resolve_report_date_local(utc_timestamp) == expected_local_date



def test_snapshot_report_date_rejects_non_utc_timestamp_inputs() -> None:
    """Reject report-date conversion inputs without timezone offsets.

    Returns:
        None: Assertions validate boundary validation behavior.

    Raises:
        AssertionError: Raised when naive timestamp is incorrectly accepted.
    """

    naive_timestamp = datetime(2026, 2, 20, 12, 0, 0).replace(tzinfo=None).isoformat()

    try:
        snapshot_resolve_report_date_local(naive_timestamp)
    except ValueError as error:
        assert "offset-aware" in str(error)
        return

    raise AssertionError("Expected ValueError for naive timestamp input")
