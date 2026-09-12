"""Task 7 FIFO ledger computation primitives."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from graphlib import CycleError, TopologicalSorter

from .snapshot_dates import snapshot_report_date_start_utc


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
        transaction_id: Broker identity used before raw row IDs for tied timestamps.
    """

    source_raw_record_id: str
    trade_timestamp_utc: str
    side: str
    quantity: Decimal
    price: Decimal
    fees: Decimal | None
    withholding_tax: Decimal | None
    event_trade_fill_id: str | None = None
    transaction_id: str | None = None
    report_date_local: date | None = None


@dataclass(frozen=True)
class FifoSplitInput:
    """Quantity adjustment effective before trades on its broker report date."""

    report_date_local: date
    adjustment_factor: Decimal


@dataclass(frozen=True)
class FifoSecurityMovementInput:
    """Dated non-trade movement, with distribution basis in functional currency."""

    event_corp_action_id: str
    source_raw_record_id: str
    source_instrument_id: str | None
    destination_instrument_id: str
    report_date_local: date
    quantity: Decimal
    cost_basis: Decimal | None


class FifoSecurityMovementError(ValueError):
    """A saved movement is incompatible with the historical position it moves."""

    def __init__(self, event_corp_action_id: str, message: str) -> None:
        super().__init__(message)
        self.event_corp_action_id = event_corp_action_id


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
    splits: tuple[FifoSplitInput, ...] = ()


@dataclass(frozen=True)
class FifoLedgerComputationResult:
    """Output payload for FIFO ledger computation.

    Attributes:
        position_quantity: Open quantity after processing all trades.
        realized_pnl: Realized PnL including trade fee/withholding impacts.
        unrealized_pnl: Unrealized PnL on open lots at mark price.
        open_lots: Open-lot details for persistence.
        closed_lots: Lots closed by trades, transfer, or split rounding, with closing times.
    """

    position_quantity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    open_lots: tuple["FifoOpenLotResult", ...]
    closed_lots: tuple["FifoOpenLotResult", ...] = ()


@dataclass(frozen=True)
class FifoOpenLotResult:
    """Lot details produced by FIFO computation for persistence layers.

    Attributes:
        open_event_trade_fill_id: Original opening trade-fill identifier, absent for distributions.
        source_raw_record_id: Opening raw row identifier.
        opened_at_utc: Opening timestamp in UTC ISO-8601 format.
        open_quantity: Original lot quantity.
        remaining_quantity: Remaining lot quantity.
        open_price: Opening trade price.
        cost_basis_open: Opening lot cost basis.
        cost_basis_remaining: Signed cost basis of the unconsumed shares.
        realized_pnl_to_date: Realized PnL posted to this lot.
        closed_report_date_local: Action date if a transfer or split rounding removed the remainder.
        closed_at_utc: Execution timestamp if a trade closed the remainder.
        open_event_corp_action_id: Distribution or transfer that introduced this instrument's lot.
        opening_transaction_id: Original broker fill identity for tied acquisition times.
        transfer_event_corp_action_id: Latest transfer, distinguishing return visits to an instrument.
        basis_source_raw_record_ids: Acquisition sources contributing basis, including split-rounding donors.
    """

    open_event_trade_fill_id: str | None
    source_raw_record_id: str
    opened_at_utc: str
    open_quantity: Decimal
    remaining_quantity: Decimal
    open_price: Decimal
    cost_basis_open: Decimal
    cost_basis_remaining: Decimal
    realized_pnl_to_date: Decimal
    closed_report_date_local: date | None = None
    closed_at_utc: str | None = None
    open_event_corp_action_id: str | None = None
    opening_transaction_id: str | None = None
    transfer_event_corp_action_id: str | None = None
    basis_source_raw_record_ids: tuple[str, ...] = ()


@dataclass
class _OpenFifoLot:
    """Mutable internal lot state used during FIFO processing."""

    direction: str
    open_event_trade_fill_id: str | None
    source_raw_record_id: str
    opened_at_utc: str
    open_quantity: Decimal
    open_price: Decimal
    cost_basis_open: Decimal
    remaining_quantity: Decimal
    unit_basis: Decimal
    unit_execution_price: Decimal
    realized_pnl_to_date: Decimal
    open_event_corp_action_id: str | None = None
    opening_transaction_id: str | None = None
    transfer_event_corp_action_id: str | None = None
    basis_source_raw_record_ids: tuple[str, ...] = ()


def fifo_compute_instrument(request: FifoLedgerComputationRequest) -> FifoLedgerComputationResult:
    """Compute one instrument from its canonical trade and split history."""
    return _fifo_compute_instrument(request)


def _fifo_compute_instrument(
    request: FifoLedgerComputationRequest,
    initial: FifoLedgerComputationResult | None = None,
) -> FifoLedgerComputationResult:
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
    if request.splits and any(trade.report_date_local is None for trade in request.trades):
        raise ValueError("split accounting requires a broker report date for each trade")
    for split in request.splits:
        if not split.adjustment_factor.is_finite() or split.adjustment_factor <= 0:
            raise ValueError("corporate-action adjustment_factor must be positive and finite")
    factors_by_date: dict[date, Decimal] = {}
    for split in sorted(request.splits, key=lambda split: (split.report_date_local, split.adjustment_factor)):
        factors_by_date[split.report_date_local] = factors_by_date.get(split.report_date_local, Decimal("1")) * split.adjustment_factor
    splits = [FifoSplitInput(day, factor) for day, factor in factors_by_date.items()]
    split_dates = list(factors_by_date)

    sorted_trades = sorted(
        request.trades,
        key=lambda trade: (
            bisect_right(split_dates, trade.report_date_local or date.min),
            _fifo_parse_timestamp_utc(trade.trade_timestamp_utc),
            (
                Decimal(trade.transaction_id)
                if trade.transaction_id is not None and trade.transaction_id.isascii()
                and trade.transaction_id.isdigit() else Decimal("-1")
            ),
            trade.transaction_id or "",
            trade.source_raw_record_id,
            trade.event_trade_fill_id or "",
        ),
    )

    open_lots = [
        _OpenFifoLot(
            direction="long" if lot.remaining_quantity > 0 else "short",
            open_event_trade_fill_id=lot.open_event_trade_fill_id,
            source_raw_record_id=lot.source_raw_record_id, opened_at_utc=lot.opened_at_utc,
            open_quantity=abs(lot.open_quantity), open_price=lot.open_price,
            cost_basis_open=lot.cost_basis_open, remaining_quantity=abs(lot.remaining_quantity),
            unit_basis=lot.cost_basis_remaining / lot.remaining_quantity,
            unit_execution_price=lot.open_price, realized_pnl_to_date=lot.realized_pnl_to_date,
            open_event_corp_action_id=lot.open_event_corp_action_id,
            opening_transaction_id=lot.opening_transaction_id,
            transfer_event_corp_action_id=lot.transfer_event_corp_action_id,
            basis_source_raw_record_ids=lot.basis_source_raw_record_ids,
        ) for lot in (initial.open_lots if initial else ())
    ]
    realized_pnl = initial.realized_pnl if initial else Decimal("0")
    split_index = 0
    closed_lots = list(initial.closed_lots) if initial else []

    for trade in sorted_trades:
        while split_index < len(splits) and splits[split_index].report_date_local <= (trade.report_date_local or date.min):
            closed_lots.extend(_fifo_split_open_lots(open_lots, splits[split_index]))
            split_index += 1
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
            current_lot.realized_pnl_to_date += lot_realized - (trade_fees + trade_withholding) * close_quantity / quantity
            matched_realized += lot_realized
            quantity_to_close -= close_quantity
            matched_quantity += close_quantity

            if current_lot.remaining_quantity == Decimal("0"):
                closed_lots.append(_fifo_lot_result(current_lot, closed_at=trade.trade_timestamp_utc))
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
                    unit_execution_price=trade.price,
                    realized_pnl_to_date=Decimal("0"),
                    opening_transaction_id=trade.transaction_id,
                    basis_source_raw_record_ids=(trade.source_raw_record_id,),
                )
            )

    for split in splits[split_index:]:
        closed_lots.extend(_fifo_split_open_lots(open_lots, split))

    open_quantity = sum(
        ((lot.remaining_quantity if lot.direction == "long" else -lot.remaining_quantity) for lot in open_lots),
        Decimal("0"),
    )
    unrealized_pnl = sum(
        ((
            (request.mark_price - lot.unit_basis) * lot.remaining_quantity
            if lot.direction == "long"
            else (lot.unit_basis - request.mark_price) * lot.remaining_quantity
        ) for lot in open_lots),
        Decimal("0"),
    )

    return FifoLedgerComputationResult(
        position_quantity=open_quantity,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        open_lots=tuple(_fifo_lot_result(lot) for lot in open_lots),
        closed_lots=tuple(closed_lots),
    )


def _fifo_lot_result(lot: _OpenFifoLot, closed_date: date | None = None, closed_at: str | None = None) -> FifoOpenLotResult:
    return FifoOpenLotResult(
        open_event_trade_fill_id=lot.open_event_trade_fill_id,
        open_event_corp_action_id=lot.open_event_corp_action_id,
        opening_transaction_id=lot.opening_transaction_id,
        transfer_event_corp_action_id=lot.transfer_event_corp_action_id,
        basis_source_raw_record_ids=lot.basis_source_raw_record_ids,
        source_raw_record_id=lot.source_raw_record_id,
        opened_at_utc=lot.opened_at_utc,
        open_quantity=lot.open_quantity if lot.direction == "long" else -lot.open_quantity,
        remaining_quantity=Decimal("0") if closed_date else (lot.remaining_quantity if lot.direction == "long" else -lot.remaining_quantity),
        open_price=lot.open_price,
        cost_basis_open=lot.cost_basis_open,
        cost_basis_remaining=Decimal("0") if closed_date else lot.unit_basis * lot.remaining_quantity * (1 if lot.direction == "long" else -1),
        realized_pnl_to_date=lot.realized_pnl_to_date,
        closed_report_date_local=closed_date,
        closed_at_utc=closed_at,
    )


def fifo_compute_portfolio(
    requests: list[FifoLedgerComputationRequest], movements: tuple[FifoSecurityMovementInput, ...],
) -> dict[str, FifoLedgerComputationResult]:
    """Replay linked instruments across movement dates, carrying their lot state."""
    if not movements:
        return {request.instrument_id: fifo_compute_instrument(request) for request in requests}
    if any(trade.report_date_local is None for request in requests for trade in request.trades):
        raise ValueError("security movements require a broker report date for each trade")
    results: dict[str, FifoLedgerComputationResult] = {}
    previous_dates: dict[str, date | None] = {}
    movement_dates = sorted({movement.report_date_local for movement in movements})
    for boundary in [*movement_dates, None]:
        for request in requests:
            if boundary is not None and not any(
                row.report_date_local == boundary
                and request.instrument_id in {row.source_instrument_id, row.destination_instrument_id}
                for row in movements
            ):
                continue
            previous_date = previous_dates.get(request.instrument_id)
            interval = replace(
                request,
                trades=[trade for trade in request.trades
                        if (previous_date is None or (trade.report_date_local or date.min) >= previous_date)
                        and (boundary is None or (trade.report_date_local or date.min) < boundary)],
                splits=tuple(split for split in request.splits
                             if (previous_date is None or split.report_date_local > previous_date)
                             and (boundary is None or split.report_date_local <= boundary)),
            )
            results[request.instrument_id] = _fifo_compute_instrument(interval, results.get(request.instrument_id))
            previous_dates[request.instrument_id] = boundary
        if boundary is None:
            break
        for movement in fifo_order_security_movements(
            [row for row in movements if row.report_date_local == boundary],
        ):
            try:
                _fifo_apply_security_movement(results, movement)
            except ValueError as error:
                raise FifoSecurityMovementError(movement.event_corp_action_id, str(error)) from error
    return results


def fifo_order_security_movements(
    movements: list[FifoSecurityMovementInput],
) -> tuple[FifoSecurityMovementInput, ...]:
    """Apply same-day incoming holdings before outgoing full-position transfers."""
    by_id = {row.event_corp_action_id: row for row in sorted(movements, key=lambda row: row.event_corp_action_id)}
    sources: set[str] = set()
    for movement in by_id.values():
        if movement.source_instrument_id is not None:
            if movement.source_instrument_id in sources:
                raise FifoSecurityMovementError(
                    movement.event_corp_action_id,
                    "same-day security transfers from the same source are ambiguous",
                )
            sources.add(movement.source_instrument_id)
    dependencies = {
        identifier: [other_id for other_id, other in by_id.items()
                     if other_id != identifier and other.destination_instrument_id == movement.source_instrument_id]
        for identifier, movement in by_id.items()
    }
    try:
        return tuple(by_id[identifier] for identifier in TopologicalSorter(dependencies).static_order())
    except CycleError as error:
        raise FifoSecurityMovementError(
            error.args[1][0], "same-day security movement dependencies are cyclic",
        ) from error


def _fifo_apply_security_movement(
    results: dict[str, FifoLedgerComputationResult], movement: FifoSecurityMovementInput,
) -> None:
    if not movement.quantity.is_finite() or movement.quantity <= 0:
        raise ValueError("security movement quantity must be positive and finite")
    if movement.source_instrument_id == movement.destination_instrument_id:
        raise ValueError("security transfer requires distinct instruments")
    if movement.source_instrument_id is not None and movement.cost_basis is not None:
        raise ValueError("security transfers carry existing basis; entered basis is unsupported")
    destination = results[movement.destination_instrument_id]
    if destination.position_quantity < 0:
        raise ValueError("security movement cannot close a destination short position")
    incoming: tuple[FifoOpenLotResult, ...]
    if movement.source_instrument_id is None:
        basis = movement.cost_basis
        if basis is None or not basis.is_finite() or basis < 0:
            raise ValueError("distribution requires explicit nonnegative finite total cost basis")
        incoming = (FifoOpenLotResult(
            open_event_trade_fill_id=None, open_event_corp_action_id=movement.event_corp_action_id,
            source_raw_record_id=movement.source_raw_record_id,
            basis_source_raw_record_ids=(movement.source_raw_record_id,),
            opened_at_utc=snapshot_report_date_start_utc(movement.report_date_local).isoformat(),
            open_quantity=movement.quantity, remaining_quantity=movement.quantity,
            open_price=basis / movement.quantity, cost_basis_open=basis, cost_basis_remaining=basis,
            realized_pnl_to_date=Decimal("0"),
        ),)
    else:
        source = results[movement.source_instrument_id]
        if source.position_quantity != movement.quantity or any(lot.remaining_quantity <= 0 for lot in source.open_lots):
            raise ValueError("security transfer must match the full long position on its action date")
        incoming = tuple(replace(
            lot, open_event_corp_action_id=(
                lot.open_event_corp_action_id if lot.open_event_trade_fill_id is None else movement.event_corp_action_id
            ),
            transfer_event_corp_action_id=movement.event_corp_action_id,
            open_quantity=lot.remaining_quantity, cost_basis_open=lot.cost_basis_remaining,
            realized_pnl_to_date=Decimal("0"),
        ) for lot in source.open_lots)
        closed = tuple(replace(lot, remaining_quantity=Decimal("0"), cost_basis_remaining=Decimal("0"),
                               closed_report_date_local=movement.report_date_local) for lot in source.open_lots)
        results[movement.source_instrument_id] = replace(
            source, position_quantity=Decimal("0"), unrealized_pnl=Decimal("0"),
            open_lots=(), closed_lots=(*source.closed_lots, *closed),
        )
    results[movement.destination_instrument_id] = replace(
        destination, position_quantity=destination.position_quantity + movement.quantity,
        open_lots=tuple(sorted((*destination.open_lots, *incoming), key=lambda lot: (
            _fifo_parse_timestamp_utc(lot.opened_at_utc),
            (Decimal(lot.opening_transaction_id)
             if lot.opening_transaction_id is not None and lot.opening_transaction_id.isascii()
             and lot.opening_transaction_id.isdigit() else Decimal("-1")),
            lot.opening_transaction_id or "",
            # Distribution provenance changes on replay; its action identity does not.
            (lot.open_event_corp_action_id if lot.open_event_trade_fill_id is None else None) or lot.source_raw_record_id,
            lot.open_event_trade_fill_id or "", lot.open_event_corp_action_id or "",
        ))),
    )


def _fifo_split_open_lots(open_lots: list[_OpenFifoLot], split: FifoSplitInput) -> list[FifoOpenLotResult]:
    """Allocate share rounding across surviving FIFO lots, preserving their basis."""
    if not open_lots:
        return []
    adjusted_quantity = Decimal("0")
    rounded_quantity = Decimal("0")
    precision = Decimal("0.00000001")
    allocations = []
    for lot in open_lots:
        adjusted_quantity += lot.remaining_quantity * split.adjustment_factor
        next_quantity = adjusted_quantity.quantize(precision)
        remaining_quantity = next_quantity - rounded_quantity
        allocations.append((lot, remaining_quantity))
        rounded_quantity = next_quantity
    survivors = [(lot, quantity) for lot, quantity in allocations if quantity > 0]
    if not survivors:
        raise ValueError("split creates a position below the supported eight-decimal share precision")
    closed_lots = [_fifo_lot_result(lot, split.report_date_local) for lot, quantity in allocations if quantity == 0]
    # A zero-share allocation must not discard its cost. Carry it into the
    # next surviving FIFO lot, or the last survivor for trailing fractions.
    recipient = survivors[-1][0]
    for lot, quantity in reversed(allocations):
        if quantity > 0:
            recipient = lot
        else:
            recipient.basis_source_raw_record_ids = tuple(sorted(set(
                recipient.basis_source_raw_record_ids + lot.basis_source_raw_record_ids,
            )))
            recipient.cost_basis_open += lot.unit_basis * lot.remaining_quantity * (1 if lot.direction == "long" else -1)
            recipient.unit_basis += lot.unit_basis * lot.remaining_quantity / recipient.remaining_quantity
            execution_value = lot.unit_execution_price * lot.remaining_quantity
            recipient.open_price += execution_value / recipient.open_quantity
            recipient.unit_execution_price += execution_value / recipient.remaining_quantity
    open_lots[:] = [lot for lot, quantity in survivors]
    for lot, remaining_quantity in survivors:
        # Use the allocated quantity to retain this lot's unconsumed cost and
        # fees. Completed closes and their realized P&L are never restated.
        lot_factor = remaining_quantity / lot.remaining_quantity
        opening_execution_value = lot.open_price * lot.open_quantity
        lot.unit_basis = lot.unit_basis * lot.remaining_quantity / remaining_quantity
        lot.unit_execution_price /= lot_factor
        lot.open_quantity = (lot.open_quantity * lot_factor).quantize(precision)
        lot.open_price = opening_execution_value / lot.open_quantity
        lot.remaining_quantity = remaining_quantity
    return closed_lots


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
