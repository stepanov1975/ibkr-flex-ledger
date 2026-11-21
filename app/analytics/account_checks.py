"""Independent ledger comparisons with explicit period and freshness boundaries."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.domain.fx_rates import ConversionRateHistory

from .account_insights import (
    AccountInsights, InsightRow, insight_check, insight_date, insight_decimal, insight_sum, position_fx_rate,
)


def account_calculation_checks(rows: list[InsightRow], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare FIFO and canonical activity, refusing incompatible periods or stale values."""
    source = AccountInsights(rows, [])
    positions = source.section("OpenPositions")
    day = positions[0].report_date if positions else None
    instruments = {i["conid"]: i for i in evidence["instruments"]}
    snapshots = {(s["instrument_id"], s["report_date_local"]): s for s in evidence["snapshots"]}
    fx_rates = ConversionRateHistory(evidence.get("fx", []))
    mutation_at = evidence["mutation"]
    broker_positions = source.open_positions
    detail_only = bool(broker_positions) and not any(position.summaries for position in broker_positions.values())
    latest_days: dict[str, date] = {}
    for snapshot in evidence["snapshots"]:
        identifier, snapshot_day = snapshot["instrument_id"], snapshot["report_date_local"]
        latest_days[identifier] = max(snapshot_day, latest_days.get(identifier, snapshot_day))
    lots_by_instrument: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for lot in evidence["lots"]:
        lots_by_instrument[lot["instrument_id"]].append(lot)
    events_by_instrument: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cash_by_instrument: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in evidence["trades"]:
        events_by_instrument[trade.get("instrument_id")].append(trade)
    for event in evidence["cash"]:
        cash_by_instrument[event["instrument_id"]].append(event)
        events_by_instrument[event["instrument_id"]].append(event)
    fifo_by_conid: dict[str, list[InsightRow]] = defaultdict(list)
    for summary in source.section("FIFOPerformanceSummaryInBase"):
        fifo_by_conid[str(summary.payload.get("conid") or "").strip()].append(summary)
    checks = []
    for conid in sorted(set(instruments) | set(broker_positions)) if positions else []:
        instrument = instruments.get(conid, {})
        identifier = instrument.get("instrument_id")
        position = broker_positions.get(conid)
        broker = position.summary if position else None
        p = broker.payload if broker else {}
        if str(instrument.get("asset_category") or "").strip().upper() in ("CASH", "FX"):
            continue
        snapshot = snapshots.get((identifier, day))
        lots = lots_by_instrument.get(identifier, [])
        related = events_by_instrument.get(identifier, [])
        fresh = latest_days.get(identifier) == day and _snapshot_fresh(snapshot, related, mutation_at, fx_rates)
        ambiguous_position = detail_only or (position is not None and broker is None)
        qty = sum((lot["remaining_quantity"] * (-1 if lot["side"] == "SELL" else 1) for lot in lots), Decimal("0")) if fresh else None
        basis = (sum((lot["cost_basis_remaining"] for lot in lots), Decimal("0"))
                 if fresh and snapshot and snapshot["currency"] == "USD" and not snapshot["provisional"] else None)
        broker_qty = insight_decimal(p.get("position")) if broker else Decimal("0")
        rate = position_fx_rate(p)
        broker_basis = insight_decimal(p.get("costBasisMoney")) if broker else Decimal("0")
        if broker and broker_basis is not None:
            broker_basis = broker_basis * rate if rate is not None else None
        value = insight_decimal(p.get("positionValue")) if broker else Decimal("0")
        market = value * rate if broker and value is not None and rate is not None else (Decimal("0") if not broker else None)
        unrealized = market - basis if market is not None and basis is not None else None
        broker_unrealized = insight_decimal(p.get("fifoPnlUnrealized")) if broker else Decimal("0")
        if broker and broker_unrealized is not None:
            broker_unrealized = broker_unrealized * rate if rate is not None else None
        if ambiguous_position:
            broker_qty = broker_basis = broker_unrealized = unrealized = None
        reason = "Independent signed FIFO lots; broker marks/FX are shared valuation inputs." if fresh else "No fresh same-date FIFO projection; rebuild required."
        money_reason = "A USD snapshot is required for this comparison." if snapshot and snapshot["currency"] != "USD" else reason
        for name, reported, computed, tolerance in (
            ("FIFO quantity", broker_qty, qty, Decimal("0.000001")),
            ("Remaining FIFO cost basis", broker_basis, basis, Decimal("0.01")),
            ("FIFO unrealized P&L", broker_unrealized, unrealized, Decimal("0.01")),
        ):
            checks.append({**insight_check(name, reported, computed, reason if name == "FIFO quantity" else money_reason, tolerance),
                           "symbol": p.get("symbol", instrument.get("symbol")),
                           "instrument_id": identifier, "currency": "units" if name == "FIFO quantity" else "USD",
                           "from_date": day, "to_date": day, "raw_id": broker.raw_id if broker else None})
        for summary in fifo_by_conid.get(conid, []):
            start, end = source.cash_report_periods.get(summary.artifact_id, (None, None))
            opening = snapshots.get((identifier, start - timedelta(days=1))) if start and start > date.min else None
            closing = snapshots.get((identifier, end)) if end else None
            gain = None
            # Cumulative economic P&L includes cash income. Remove only fully convertible
            # canonical cash impacts before comparing period trading P&L.
            cash_impacts = [_cash_base(e, fx_rates) for e in cash_by_instrument.get(identifier, [])
                            if start and end and start <= e["report_date_local"] <= end]
            net_income = insight_sum(cash_impacts)
            if (fresh and opening and closing and _snapshot_fresh(opening, related, mutation_at, fx_rates)
                    and opening["currency"] == closing["currency"] == "USD"
                    and not opening["provisional"] and not closing["provisional"]
                    and end == day and net_income is not None):
                gain = closing["realized_pnl"] - opening["realized_pnl"] - net_income
            checks.append({**insight_check("Period realized trading P&L", insight_decimal(summary.payload.get("totalRealizedPnl")), gain,
                            "Requires exact USD opening/closing snapshots and convertible cash impacts."),
                           "symbol": p.get("symbol", instrument.get("symbol")), "instrument_id": identifier,
                           "currency": "USD", "from_date": start, "to_date": end, "raw_id": summary.raw_id})
            checks.append({**insight_check("Summary unrealized P&L", insight_decimal(summary.payload.get("totalUnrealizedPnl")),
                                           unrealized if end == day else None, money_reason),
                           "symbol": p.get("symbol", instrument.get("symbol")), "instrument_id": identifier,
                           "currency": "USD", "from_date": end, "to_date": end, "raw_id": summary.raw_id})
    checks.extend(_cash_checks(source, evidence))
    for row in source.section("MTMPerformanceSummaryInBase"):
        p = row.payload
        if not p.get("conid"):
            continue
        start, end = source.cash_report_periods.get(row.artifact_id, (None, None))
        checks.append({**insight_check("MTM component arithmetic", insight_decimal(p.get("total")),
                        insight_sum([insight_decimal(p.get(key)) for key in ("priorOpenMtm", "transactionMtm", "commissions", "other")]),
                        "Broker internal arithmetic, not an independent FIFO comparison."),
                       "symbol": p.get("symbol"), "currency": "USD", "from_date": start, "to_date": end, "raw_id": row.raw_id})
    return checks


def _cash_base(event: dict[str, Any], fx_rates: ConversionRateHistory) -> Decimal | None:
    """Use the same USD base amount or dated conversion as the snapshot."""
    currency = str(event["currency"]).strip().upper()
    functional_currency = str(event.get("functional_currency") or "").strip().upper()
    amount = event["amount_in_base"] if functional_currency == "USD" else None
    costs = (event["fees"] or Decimal("0")) + (event["withholding_tax"] or Decimal("0"))
    if amount is not None and costs == 0:
        return amount
    rate: Decimal | None
    if currency == "USD":
        rate = Decimal("1")
    else:
        selected = fx_rates.select(currency, "USD", event["report_date_local"])
        rate = insight_decimal(selected.fx_rate) if selected else None
    if rate is None:
        return None
    return (amount if amount is not None else event["amount"] * rate) - costs * rate


def _cash_checks(source: AccountInsights, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Reconstruct native cash from booked canonical trades and cash transactions."""
    checks = []
    for row in source.section("CashReport"):
        p = row.payload
        currency = str(p.get("currency") or "").strip().upper()
        if not currency or currency == "BASE_SUMMARY":
            continue
        start, end = insight_date(p.get("fromDate")), insight_date(p.get("toDate"))
        valid_period = start is not None and end is not None and start <= end
        trades = [e for e in evidence["trades"] if start and end and start <= e["report_date_local"] <= end]
        cash = [e for e in evidence["cash"] if e["currency"] == currency
                and start and end and start <= e["report_date_local"] <= end]
        values = [e["net_cash"] for e in trades if e["currency"] == currency]
        values += [e["amount"] - (e["fees"] or Decimal("0")) - (e["withholding_tax"] or Decimal("0")) for e in cash]
        # FX executions have a second currency cash leg not represented by net_cash.
        unsupported = any(
            str(e["payload"].get("assetCategory") or "").strip().upper() in ("CASH", "FX")
            or str(e["payload"].get("ibCommissionCurrency") or "").strip().upper() not in ("", e["currency"])
            for e in trades
        )
        value = insight_sum([insight_decimal(p.get("startingCash")), *values]) if valid_period and not unsupported else None
        checks.append({**insight_check("Canonical cash rollforward", insight_decimal(p.get("endingCash")), value,
                        "Requires a valid CashReport period." if not valid_period else
                        "Opening cash + canonical trade net cash + cashflows; differences can identify missing activity."
                        if not unsupported else "FX cash legs or cross-currency commissions require additional accounting."),
                       "symbol": "Account", "currency": currency, "from_date": start, "to_date": end, "raw_id": row.raw_id})
    return checks


def _snapshot_fresh(
    snapshot: dict[str, Any] | None, events: list[dict[str, Any]],
    mutation_at: datetime | None, fx_rates: ConversionRateHistory,
) -> bool:
    """Require known calculation provenance and unchanged consumed inputs at each boundary."""
    if not snapshot or snapshot.get("calculated_at_utc") is None or snapshot.get("fx_dependencies") is None:
        return False
    changed = [e["updated_at_utc"] for e in events if e["report_date_local"] <= snapshot["report_date_local"]]
    if mutation_at is not None:
        changed.append(mutation_at)
    if any(value > snapshot["calculated_at_utc"] for value in changed):
        return False
    for dependency in snapshot["fx_dependencies"]:
        selected = fx_rates.select(dependency["currency"], dependency["functional_currency"],
                                   date.fromisoformat(dependency["date"]))
        if (insight_decimal(selected.fx_rate) if selected else None) != insight_decimal(dependency["rate"]):
            return False
    return True
