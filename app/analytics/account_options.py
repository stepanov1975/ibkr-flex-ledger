"""Option lifecycle links to existing canonical executions without synthetic trades."""

from decimal import Decimal
from typing import Any

from .account_insights import AccountInsights, InsightRow, insight_check, insight_date, insight_decimal


def account_option_activity(rows: list[InsightRow], trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Link broker lifecycle rows only through unique trade/security identities."""
    lifecycle = [r for r in AccountInsights(rows, []).section("OptionEAE") if r.payload.get("conid")]
    result = []
    for row in lifecycle:
        p = row.payload
        conid, trade_id = str(p["conid"]), str(p.get("tradeID") or "")
        candidates = [t for t in trades if trade_id and str(t["payload"].get("tradeID")) == trade_id
                      and str(t["payload"].get("conid")) == conid and t["currency"] == p.get("currency")]
        trade = candidates[0] if len(candidates) == 1 else None
        quantity, price = (trade["quantity"], trade["price"]) if trade else (None, None)
        signed = abs(quantity) * (-1 if trade["side"] == "SELL" else 1) if quantity is not None and trade else None
        multiplier = insight_decimal(p.get("multiplier"))
        proceeds = -signed * price * multiplier if signed is not None and price is not None and multiplier is not None and multiplier > 0 else None
        reason = "Unique canonical trade identity." if trade else "Missing or ambiguous canonical trade identity."
        item = {"symbol": p.get("symbol"), "conid": conid, "date": insight_date(p.get("date")),
                "type": p.get("transactionType"), "quantity": insight_decimal(p.get("quantity")),
                "currency": p.get("currency"), "proceeds": insight_decimal(p.get("proceeds")),
                "multiplier": multiplier, "instrument_id": trade["instrument_id"] if trade else None,
                "event_id": trade["event_id"] if trade else None, "raw_id": row.raw_id,
                "quantity_check": insight_check("Lifecycle quantity", insight_decimal(p.get("quantity")), signed,
                                                reason, Decimal("0.000001")),
                "cash_check": insight_check("Lifecycle gross proceeds", insight_decimal(p.get("proceeds")), proceeds,
                                            reason + " Gross proceeds exclude commissions and taxes."),
                "delivery": None}
        action = str(p.get("transactionType") or "").lower()
        if p.get("assetCategory") == "OPT" and action in ("assignment", "exercise"):
            underlying = str(p.get("underlyingConid") or "")
            legs = [r for r in lifecycle if underlying and str(r.payload.get("conid")) == underlying
                    and insight_date(r.payload.get("date")) == item["date"] and r.payload.get("currency") == p.get("currency")
                    and str(r.payload.get("transactionType") or "").lower() in ("buy", "sell")]
            related_options = [r for r in lifecycle if str(r.payload.get("underlyingConid") or "") == underlying
                               and insight_date(r.payload.get("date")) == item["date"]
                               and r.payload.get("assetCategory") == "OPT"
                               and str(r.payload.get("transactionType") or "").lower() in ("assignment", "exercise")]
            option_qty = insight_decimal(p.get("quantity"))
            expected = None
            if (option_qty is not None and multiplier is not None and multiplier > 0
                    and p.get("putCall") in ("P", "C") and item["date"] is not None):
                direction = 1 if (action == "assignment" and p["putCall"] == "P") or (action == "exercise" and p["putCall"] == "C") else -1
                expected = abs(option_qty) * multiplier * direction
            delivered = insight_decimal(legs[0].payload.get("quantity")) if len(legs) == len(related_options) == 1 else None
            item["delivery"] = insight_check("Underlying delivery quantity", delivered, expected,
                "Unique same-date underlying leg using the reported multiplier; adjusted deliverables may require review.",
                Decimal("0.000001"))
        result.append(item)
    return result
