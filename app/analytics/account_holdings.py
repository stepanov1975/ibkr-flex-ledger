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
