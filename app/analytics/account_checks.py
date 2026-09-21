"""Independent ledger comparisons with explicit period and freshness boundaries."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.domain.fx_rates import select_conversion_rate

from .account_insights import AccountInsights, InsightRow, insight_check, insight_date, insight_decimal, insight_sum


def account_calculation_checks(rows: list[InsightRow], evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare FIFO and canonical activity, refusing incompatible periods or stale values."""
    source = AccountInsights(rows, [])
    positions = source.section("OpenPositions")
    day = positions[0].report_date if positions else None
    instruments = {i["conid"]: i for i in evidence["instruments"]}
    snapshots = {(s["instrument_id"], s["report_date_local"]): s for s in evidence["snapshots"]}
    broker_positions = {str(r.payload["conid"]): r for r in positions
                        if r.payload.get("conid") and r.payload.get("levelOfDetail", "SUMMARY") == "SUMMARY"
                        and r.payload.get("assetCategory") not in ("CASH", "FX")}
    checks = []
    for conid in sorted(set(instruments) | set(broker_positions)) if positions else []:
        instrument = instruments.get(conid, {})
        identifier = instrument.get("instrument_id")
        broker = broker_positions.get(conid)
        p = broker.payload if broker else {}
        if instrument.get("asset_category") in ("CASH", "FX"):
            continue
        snapshot = snapshots.get((identifier, day))
        lots = [lot for lot in evidence["lots"] if lot["instrument_id"] == identifier]
        related = [e for e in [*evidence["trades"], *evidence["cash"]] if e["instrument_id"] == identifier]
        latest_day = max((s["report_date_local"] for s in evidence["snapshots"]
                          if s["instrument_id"] == identifier), default=None)
        fresh = latest_day == day and _snapshot_fresh(snapshot, related, evidence)
        summaries = [r for r in positions if str(r.payload.get("conid")) == conid
                     and r.payload.get("levelOfDetail", "SUMMARY") == "SUMMARY"]
        detail_only = any(r.payload.get("conid") for r in positions) and not broker_positions
        ambiguous_position = len(summaries) > 1 or detail_only or (
            not broker and any(str(r.payload.get("conid")) == conid for r in positions))
        qty = sum((lot["remaining_quantity"] * (-1 if lot["side"] == "SELL" else 1) for lot in lots), Decimal("0")) if fresh else None
        basis = sum((lot["cost_basis_remaining"] for lot in lots), Decimal("0")) if fresh and snapshot and not snapshot["provisional"] else None
        broker_qty = insight_decimal(p.get("position")) if broker else Decimal("0")
        rate = Decimal("1") if p.get("currency") == "USD" else insight_decimal(p.get("fxRateToBase"))
        broker_basis = insight_decimal(p.get("costBasisMoney")) if broker else Decimal("0")
        if broker and broker_basis is not None:
            broker_basis = broker_basis * rate if rate is not None and rate > 0 else None
        value = insight_decimal(p.get("positionValue")) if broker else Decimal("0")
        market = value * rate if broker and value is not None and rate is not None and rate > 0 else (Decimal("0") if not broker else None)
        unrealized = market - basis if market is not None and basis is not None else None
        broker_unrealized = insight_decimal(p.get("fifoPnlUnrealized")) if broker else Decimal("0")
        if broker and broker_unrealized is not None:
            broker_unrealized = broker_unrealized * rate if rate is not None and rate > 0 else None
        if ambiguous_position:
            broker_qty = broker_basis = broker_unrealized = unrealized = None
        reason = "Independent signed FIFO lots; broker marks/FX are shared valuation inputs." if fresh else "No fresh same-date FIFO projection; rebuild required."
        for name, reported, computed, tolerance in (
            ("FIFO quantity", broker_qty, qty, Decimal("0.000001")),
            ("Remaining FIFO cost basis", broker_basis, basis, Decimal("0.01")),
            ("FIFO unrealized P&L", broker_unrealized, unrealized, Decimal("0.01")),
        ):
            checks.append({**insight_check(name, reported, computed, reason, tolerance), "symbol": p.get("symbol", instrument.get("symbol")),
                           "instrument_id": identifier, "currency": "units" if name == "FIFO quantity" else "USD",
                           "from_date": day, "to_date": day, "raw_id": broker.raw_id if broker else None})
        for summary in source.section("FIFOPerformanceSummaryInBase"):
            if str(summary.payload.get("conid")) != conid:
                continue
            start, end = _period(source, summary)
            opening = snapshots.get((identifier, start - timedelta(days=1))) if start else None
            closing = snapshots.get((identifier, end)) if end else None
            gain = None
            # Cumulative economic P&L includes cash income. Remove only fully convertible
            # canonical cash impacts before comparing period trading P&L.
            cash_impacts = [_cash_base(e) for e in evidence["cash"]
                            if e["instrument_id"] == identifier and start and end
                            and start <= e["report_date_local"] <= end]
            net_income = insight_sum(cash_impacts)
            if (fresh and opening and closing and _snapshot_fresh(opening, related, evidence) and not opening["provisional"] and not closing["provisional"]
                    and end == day and net_income is not None):
                gain = closing["realized_pnl"] - opening["realized_pnl"] - net_income
            checks.append({**insight_check("Period realized trading P&L", insight_decimal(summary.payload.get("totalRealizedPnl")), gain,
                            "Requires exact opening/closing snapshots and convertible cash impacts; cumulative P&L is not period P&L."),
                           "symbol": p.get("symbol", instrument.get("symbol")), "instrument_id": identifier,
                           "currency": "USD", "from_date": start, "to_date": end, "raw_id": summary.raw_id})
            checks.append({**insight_check("Summary unrealized P&L", insight_decimal(summary.payload.get("totalUnrealizedPnl")),
                                           unrealized if end == day else None, reason),
                           "symbol": p.get("symbol", instrument.get("symbol")), "instrument_id": identifier,
                           "currency": "USD", "from_date": end, "to_date": end, "raw_id": summary.raw_id})
    checks.extend(_cash_checks(source, evidence))
    for row in source.section("MTMPerformanceSummaryInBase"):
        p = row.payload
        if not p.get("conid"):
            continue
        start, end = _period(source, row)
        checks.append({**insight_check("MTM component arithmetic", insight_decimal(p.get("total")),
                        insight_sum([insight_decimal(p.get(key)) for key in ("priorOpenMtm", "transactionMtm", "commissions", "other")]),
                        "Broker internal arithmetic, not an independent FIFO comparison."),
                       "symbol": p.get("symbol"), "currency": "USD", "from_date": start, "to_date": end, "raw_id": row.raw_id})
    return checks


def _period(source: AccountInsights, row: InsightRow) -> tuple[date | None, date | None]:
    """Use only explicit period metadata from the same artifact."""
    candidates = [r for r in source.section("CashReport") if r.artifact_id == row.artifact_id]
    p = candidates[0].payload if candidates else {}
    return insight_date(p.get("fromDate")), insight_date(p.get("toDate"))


def _cash_base(event: dict[str, Any]) -> Decimal | None:
    """Match economic cash impact only when its currency conversion is explicit."""
    rate = Decimal("1") if event["currency"] == "USD" else insight_decimal(event["payload"].get("fxRateToBase"))
    amount = event["amount_in_base"]
    if amount is None:
        amount = event["amount"] * rate if rate is not None else None
    costs = (event["fees"] or Decimal("0")) + (event["withholding_tax"] or Decimal("0"))
    return amount - costs * rate if amount is not None and rate is not None else None


def _cash_checks(source: AccountInsights, evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Reconstruct native cash from booked canonical trades and cash transactions."""
    checks = []
    for row in source.section("CashReport"):
        p = row.payload
        currency = p.get("currency")
        if not currency or currency == "BASE_SUMMARY":
            continue
        start, end = insight_date(p.get("fromDate")), insight_date(p.get("toDate"))
        trades = [e for e in evidence["trades"] if start and end and start <= e["report_date_local"] <= end]
        cash = [e for e in evidence["cash"] if e["currency"] == currency
                and start and end and start <= e["report_date_local"] <= end]
        values = [e["net_cash"] for e in trades if e["currency"] == currency]
        values += [e["amount"] - (e["fees"] or Decimal("0")) - (e["withholding_tax"] or Decimal("0")) for e in cash]
        # FX executions have a second currency cash leg not represented by net_cash.
        unsupported = any(e["payload"].get("assetCategory") in ("CASH", "FX")
                          or e["payload"].get("ibCommissionCurrency", e["currency"]) not in ("", e["currency"])
                          for e in trades)
        value = insight_sum([insight_decimal(p.get("startingCash")), *values]) if start and end and not unsupported else None
        checks.append({**insight_check("Canonical cash rollforward", insight_decimal(p.get("endingCash")), value,
                        "Opening cash + canonical trade net cash + cashflows; differences can identify missing activity."
                        if not unsupported else "FX cash legs or cross-currency commissions require additional accounting."),
                       "symbol": "Account", "currency": currency, "from_date": start, "to_date": end, "raw_id": row.raw_id})
    return checks


def _snapshot_fresh(snapshot: dict[str, Any] | None, events: list[dict[str, Any]],
                    evidence: dict[str, Any]) -> bool:
    """Require known calculation provenance and unchanged consumed inputs at each boundary."""
    if not snapshot or snapshot.get("calculated_at_utc") is None or snapshot.get("fx_dependencies") is None:
        return False
    changed = [e["updated_at_utc"] for e in events if e["report_date_local"] <= snapshot["report_date_local"]]
    if evidence["mutation"]:
        changed.append(evidence["mutation"])
    if any(value > snapshot["calculated_at_utc"] for value in changed):
        return False
    for dependency in snapshot["fx_dependencies"]:
        selected = select_conversion_rate(dependency["currency"], dependency["functional_currency"],
                                          date.fromisoformat(dependency["date"]), evidence.get("fx", []))
        if (insight_decimal(selected.fx_rate) if selected else None) != insight_decimal(dependency["rate"]):
            return False
    return True
