"""Carry the reported option premium into a uniquely identified stock delivery."""

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from app.db.interfaces import LedgerTradeFillRecord


def _number(value: str | None) -> Decimal | None:
    try:
        number = Decimal((value or "").replace(",", ""))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def _delivery_codes(payload: dict[str, str]) -> set[str]:
    return {"A", "Ex"}.intersection(payload.get("notes", "").replace(",", " ").replace(";", " ").split())


def delivery_price_adjustments(trades: list[LedgerTradeFillRecord], base_currency: str) -> dict[UUID, Decimal | None]:
    """Return signed base-currency adjustments per contract/share, or unknown for unsupported deliveries."""
    groups: dict[tuple[date, str, str], list[LedgerTradeFillRecord]] = defaultdict(list)
    adjustments: dict[UUID, Decimal | None] = {}
    for trade in trades:
        p = trade.delivery
        if p is None:
            continue
        adjustments[trade.event_trade_fill_id] = None
        conid = p.get("underlyingConid") if trade.asset_category == "OPT" else p.get("conid")
        if conid:
            groups[trade.report_date_local, conid, trade.currency].append(trade)

    for (_, _, currency), legs in groups.items():
        options = [leg for leg in legs if leg.asset_category == "OPT"]
        stocks = [leg for leg in legs if leg.asset_category == "STK"]
        if len(legs) != 2 or len(options) != 1 or len(stocks) != 1 or currency != base_currency:
            continue
        option, stock = options[0], stocks[0]
        details = option.delivery or {}
        stock_details = stock.delivery or {}
        action = _delivery_codes(details)
        if len(action) != 1 or action != _delivery_codes(stock_details):
            continue
        cost = _number(details.get("cost"))
        multiplier = _number(option.multiplier)
        contracts, shares = abs(Decimal(option.quantity)), abs(Decimal(stock.quantity))
        put_call = details.get("putCall")
        assigned = "A" in action
        buy_stock = (assigned and put_call == "P") or (not assigned and put_call == "C")
        if cost is None or multiplier is None or contracts == 0 or shares == 0 or contracts * multiplier != shares:
            continue
        if put_call not in {"P", "C"} or action != ({"A"} if option.side == "BUY" else {"Ex"}):
            continue
        if (stock.side != ("BUY" if buy_stock else "SELL") or _number(details.get("strike")) != Decimal(stock.price)
                or Decimal(stock.multiplier or "1") != 1):
            continue
        if (Decimal(option.price) != 0 or _number(details.get("fifoPnlRealized")) != 0
                or (assigned and cost < 0) or (not assigned and cost > 0)):
            continue
        # The option's signed closing cost already includes its opening commission.
        adjustments[option.event_trade_fill_id] = abs(cost) / contracts
        adjustments[stock.event_trade_fill_id] = (-cost if buy_stock else cost) / shares
    return adjustments
