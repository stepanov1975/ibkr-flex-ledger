"""Task 7 FIFO ledger computation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class FifoTradeFillInput:
    """Trade-fill input contract for FIFO ledger computation.

    Attributes:
        event_trade_fill_id: Optional canonical trade-fill identifier.
        source_raw_record_id: Source raw row identifier used for deterministic tie-break ordering.
        trade_timestamp_utc: Trade timestamp in UTC ISO-8601 format.
        side: Trade side (`BUY` or `SELL`).
        quantity: Trade quantity.
        price: Trade price.
        fees: Optional fee impact associated with the trade.
        withholding_tax: Optional withholding-tax impact associated with the trade.
    """

    source_raw_record_id: str
    trade_timestamp_utc: str
    side: str
    quantity: Decimal
    price: Decimal
    fees: Decimal | None
    withholding_tax: Decimal | None
    event_trade_fill_id: str | None = None


@dataclass(frozen=True)
class FifoLedgerComputationRequest:
    """Input contract for one instrument FIFO ledger computation.

    Attributes:
        account_id: Internal account context identifier.
        instrument_id: Canonical instrument identifier.
        functional_currency: Functional/base currency code.
        mark_price: End-of-day mark price used for unrealized PnL.
        trades: Ordered or unordered trade-fill inputs.
    """

    account_id: str
    instrument_id: str
    functional_currency: str
    mark_price: Decimal
    trades: list[FifoTradeFillInput]


@dataclass(frozen=True)
class FifoLedgerComputationResult:
    """Output payload for FIFO ledger computation.

    Attributes:
        position_quantity: Open quantity after processing all trades.
        realized_pnl: Realized PnL including trade fee/withholding impacts.
        unrealized_pnl: Unrealized PnL on open lots at mark price.
        open_lots: Open-lot details for persistence.
    """

    position_quantity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    open_lots: tuple["FifoOpenLotResult", ...]


@dataclass(frozen=True)
class FifoOpenLotResult:
    """Open-lot details produced by FIFO computation for persistence layers.

    Attributes:
        open_event_trade_fill_id: Opening trade-fill identifier.
        source_raw_record_id: Opening raw row identifier.
        opened_at_utc: Opening timestamp in UTC ISO-8601 format.
        open_quantity: Original lot quantity.
        remaining_quantity: Remaining lot quantity.
        open_price: Opening trade price.
        cost_basis_open: Opening lot cost basis.
        realized_pnl_to_date: Realized PnL posted to this lot.
    """

    open_event_trade_fill_id: str
    source_raw_record_id: str
    opened_at_utc: str
    open_quantity: Decimal
    remaining_quantity: Decimal
    open_price: Decimal
    cost_basis_open: Decimal
    realized_pnl_to_date: Decimal


@dataclass
class _OpenFifoLot:
    """Mutable internal lot state used during FIFO processing."""

    direction: str
    open_event_trade_fill_id: str
    source_raw_record_id: str
    opened_at_utc: str
    open_quantity: Decimal
    open_price: Decimal
    cost_basis_open: Decimal
    remaining_quantity: Decimal
    unit_basis: Decimal
    realized_pnl_to_date: Decimal


def fifo_compute_instrument(request: FifoLedgerComputationRequest) -> FifoLedgerComputationResult:  # pylint: disable=too-many-statements
    """Compute FIFO realized and unrealized PnL for one instrument.

    Args:
        request: FIFO computation request.

    Returns:
        FifoLedgerComputationResult: Deterministic per-instrument FIFO outputs.

    Raises:
        ValueError: Raised when request data or trade ordering inputs are invalid.
    """

    if request is None:
        raise ValueError("request must not be None")
    if not request.account_id.strip():
        raise ValueError("request.account_id must not be blank")
    if not request.instrument_id.strip():
        raise ValueError("request.instrument_id must not be blank")
    if not request.functional_currency.strip():
        raise ValueError("request.functional_currency must not be blank")

    sorted_trades = sorted(
        request.trades,
        key=lambda trade: (
            _fifo_parse_timestamp_utc(trade.trade_timestamp_utc),
            trade.source_raw_record_id,
        ),
    )

    open_lots: list[_OpenFifoLot] = []
    realized_pnl = Decimal("0")

    for trade in sorted_trades:
        side = trade.side.strip().upper()
        quantity = abs(trade.quantity)
        if quantity == Decimal("0"):
            continue

        trade_fees = trade.fees or Decimal("0")
        trade_withholding = trade.withholding_tax or Decimal("0")

        if side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported trade side={trade.side}")

        opens_direction = "long" if side == "BUY" else "short"
        closes_direction = "short" if opens_direction == "long" else "long"

        quantity_to_close = quantity
        matched_quantity = Decimal("0")
        matched_realized = Decimal("0")

        while quantity_to_close > Decimal("0") and open_lots and open_lots[0].direction == closes_direction:
            current_lot = open_lots[0]
            close_quantity = min(quantity_to_close, current_lot.remaining_quantity)
            if closes_direction == "long":
                lot_realized = (trade.price - current_lot.unit_basis) * close_quantity
            else:
                lot_realized = (current_lot.unit_basis - trade.price) * close_quantity

            current_lot.remaining_quantity -= close_quantity
            current_lot.realized_pnl_to_date += lot_realized
            matched_realized += lot_realized
            quantity_to_close -= close_quantity
            matched_quantity += close_quantity

            if current_lot.remaining_quantity == Decimal("0"):
                open_lots.pop(0)

        if matched_quantity > Decimal("0"):
            fee_ratio = matched_quantity / quantity if quantity != Decimal("0") else Decimal("0")
            allocated_close_fees = (trade_fees + trade_withholding) * fee_ratio
            realized_pnl += matched_realized - allocated_close_fees

        if quantity_to_close > Decimal("0"):
            open_fee_ratio = quantity_to_close / quantity if quantity != Decimal("0") else Decimal("0")
            allocated_open_fees = (trade_fees + trade_withholding) * open_fee_ratio
            if opens_direction == "long":
                unit_basis = ((trade.price * quantity_to_close) + allocated_open_fees) / quantity_to_close
                signed_open_quantity = quantity_to_close
            else:
                unit_basis = ((trade.price * quantity_to_close) - allocated_open_fees) / quantity_to_close
                signed_open_quantity = -quantity_to_close

            open_event_trade_fill_id = (trade.event_trade_fill_id or trade.source_raw_record_id).strip()
            if not open_event_trade_fill_id:
                raise ValueError("trade open event identifier must not be blank")

            open_lots.append(
                _OpenFifoLot(
                    direction=opens_direction,
                    open_event_trade_fill_id=open_event_trade_fill_id,
                    source_raw_record_id=trade.source_raw_record_id,
                    opened_at_utc=trade.trade_timestamp_utc,
                    open_quantity=quantity_to_close,
                    open_price=trade.price,
                    cost_basis_open=unit_basis * signed_open_quantity,
                    remaining_quantity=quantity_to_close,
                    unit_basis=unit_basis,
                    realized_pnl_to_date=Decimal("0"),
                )
            )

    open_quantity = sum(
        (lot.remaining_quantity if lot.direction == "long" else -lot.remaining_quantity)
        for lot in open_lots
    )
    unrealized_pnl = sum(
        (
            (request.mark_price - lot.unit_basis) * lot.remaining_quantity
            if lot.direction == "long"
            else (lot.unit_basis - request.mark_price) * lot.remaining_quantity
        )
        for lot in open_lots
    )

    return FifoLedgerComputationResult(
        position_quantity=open_quantity,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        open_lots=tuple(
            FifoOpenLotResult(
                open_event_trade_fill_id=lot.open_event_trade_fill_id,
                source_raw_record_id=lot.source_raw_record_id,
                opened_at_utc=lot.opened_at_utc,
                open_quantity=lot.open_quantity if lot.direction == "long" else -lot.open_quantity,
                remaining_quantity=lot.remaining_quantity if lot.direction == "long" else -lot.remaining_quantity,
                open_price=lot.open_price,
                cost_basis_open=lot.cost_basis_open,
                realized_pnl_to_date=lot.realized_pnl_to_date,
            )
            for lot in open_lots
        ),
    )


def _fifo_parse_timestamp_utc(timestamp_value: str) -> datetime:
    """Parse UTC timestamp for deterministic FIFO sorting.

    Args:
        timestamp_value: UTC ISO-8601 timestamp string.

    Returns:
        datetime: Parsed offset-aware UTC timestamp.

    Raises:
        ValueError: Raised when timestamp is blank, invalid, or offset-naive.
    """

    if not isinstance(timestamp_value, str) or not timestamp_value.strip():
        raise ValueError("trade_timestamp_utc must be a non-empty string")

    try:
        parsed_timestamp = datetime.fromisoformat(timestamp_value)
    except ValueError as error:
        raise ValueError(f"invalid trade_timestamp_utc={timestamp_value}") from error

    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
        raise ValueError("trade_timestamp_utc must be offset-aware")

    return parsed_timestamp

