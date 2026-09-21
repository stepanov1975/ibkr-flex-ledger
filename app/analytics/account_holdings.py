"""Broker holdings, securities lending and cash visibility."""

from decimal import Decimal
from typing import Any

from .account_insights import AccountInsights, InsightRow, insight_check, insight_decimal, insight_sum


def account_lending(rows: list[InsightRow]) -> dict[str, Any]:
    """Keep signed lending arithmetic distinct from owned investment quantities."""
    source = AccountInsights(rows, [])
    holdings = []
    for row in source.section("NetStockPositionSummary"):
        p = row.payload
        if not p.get("conid"):
            continue
        owned, borrowed, lent, net = [insight_decimal(p.get(k)) for k in
                                      ("sharesAtIb", "sharesBorrowed", "sharesLent", "netShares")]
        magnitude = abs(lent) if lent is not None else None
        percent = magnitude / owned * 100 if magnitude is not None and owned is not None and owned > 0 else None
        matches = [r for r in source.section("OpenPositions") if r.payload.get("conid") == p["conid"]
                   and r.payload.get("levelOfDetail", "SUMMARY") == "SUMMARY" and r.report_date == row.report_date]
        position = insight_decimal(matches[0].payload.get("position")) if len(matches) == 1 else None
        holdings.append({"symbol": p.get("symbol"), "conid": p["conid"], "date": row.report_date,
                         "owned": owned, "borrowed": borrowed, "lent": magnitude, "lent_signed": lent,
                         "net": net, "lent_percent": percent, "raw_id": row.raw_id,
                         "net_check": insight_check("Lending shares arithmetic", net, insight_sum([owned, borrowed, lent]),
                                                    "Owned + signed borrowed + signed lent.", Decimal("0.000001")),
                         "holding_check": insight_check("Owned shares versus broker position", position, owned,
                                                        "Same-date broker sections; not an independent ledger check.",
                                                        Decimal("0.000001"))})
    nav = source.nav()
    collateral = [] if not nav else [c for c in nav["components"] if c["field"] in
                                     ("slbCashCollateral", "slbDirectSecuritiesBorrowed", "slbDirectSecuritiesLent")]
    return {"holdings": holdings, "collateral": collateral,
            "collateral_date": nav["date"] if nav else None, "currency": nav["currency"] if nav else None}


def account_settled_cash(rows: list[InsightRow]) -> list[dict[str, Any]]:
    """Show native cash settlement without treating base summaries as extra cash."""
    from .account_insights import insight_date

    result = []
    for row in AccountInsights(rows, []).section("CashReport"):
        p = row.payload
        currency = str(p.get("currency") or "").strip().upper()
        if not currency or currency == "BASE_SUMMARY":
            continue
        ending, settled = insight_decimal(p.get("endingCash")), insight_decimal(p.get("endingSettledCash"))
        result.append({"currency": currency, "from_date": insight_date(p.get("fromDate")),
                       "to_date": insight_date(p.get("toDate")), "report_date": row.report_date,
                       "ending": ending, "settled": settled,
                       "unsettled": ending - settled if ending is not None and settled is not None else None,
                       "raw_id": row.raw_id})
    return result


def account_concentration(rows: list[InsightRow]) -> dict[str, Any]:
    """Show signed market-value weights without implying derivative delta exposure."""
    source = AccountInsights(rows, [])
    nav = source.nav()
    total = nav["total"] if nav else None
    holdings = []
    positions = source.section("OpenPositions")
    summaries = [r for r in positions if r.payload.get("conid")
                 and r.payload.get("levelOfDetail", "SUMMARY") == "SUMMARY"
                 and r.payload.get("assetCategory") not in ("CASH", "FX")]
    for row in summaries:
        p = row.payload
        value, rate = insight_decimal(p.get("positionValue")), insight_decimal(p.get("fxRateToBase"))
        currency = str(p.get("currency") or "").strip().upper()
        if currency == "USD":
            rate = Decimal("1")
        unique = sum(r.payload["conid"] == p["conid"] for r in summaries) == 1
        base = value * rate if unique and value is not None and rate is not None and rate > 0 else None
        aligned = nav and nav["currency"] == "USD" and nav["date"] == row.report_date
        percent = base / total * 100 if aligned and base is not None and total is not None and total > 0 else None
        broker_percent = insight_decimal(p.get("percentOfNAV"))
        holdings.append({"symbol": p.get("symbol"), "conid": p["conid"], "asset_category": p.get("assetCategory"),
                         "currency": currency, "date": row.report_date, "value_usd": base, "class_percent": None,
                         "broker_percent": broker_percent, "calculated_percent": percent, "raw_id": row.raw_id,
                         "check": insight_check("Broker asset-class weight (%)", broker_percent, None,
                                                "Asset-class identity is required for the broker percentage check.")})
    holdings.sort(key=lambda r: (r["value_usd"] is None, -abs(r["value_usd"] or Decimal("0")), str(r["symbol"])))
    allocations = []
    for category in sorted({r["asset_category"] for r in holdings if r["asset_category"]}):
        selected = [r for r in holdings if r["asset_category"] == category]
        class_total = insight_sum([r["value_usd"] for r in selected])
        for holding in selected:
            class_percent = (holding["value_usd"] / class_total * 100
                             if holding["value_usd"] is not None and class_total not in (None, Decimal("0")) else None)
            holding["class_percent"] = class_percent
            holding["check"] = insight_check("Broker asset-class weight (%)", holding["broker_percent"], class_percent,
                "IBKR percentOfNAV divides position value by its asset-class total, not whole-account NAV.")
        allocations.append({"asset_category": category, "value_usd": class_total,
                            "percent": insight_sum([r["calculated_percent"] for r in selected])})
    currencies = []
    for currency in sorted({r["currency"] for r in holdings if r["currency"]}):
        selected = [r for r in holdings if r["currency"] == currency]
        currencies.append({"currency": currency, "value_usd": insight_sum([r["value_usd"] for r in selected]),
                           "percent": insight_sum([r["calculated_percent"] for r in selected])})
    return {"holdings": holdings, "asset_allocation": allocations, "currency_allocation": currencies,
            "nav_date": nav["date"] if nav else None, "nav": total}
