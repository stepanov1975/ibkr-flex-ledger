"""Execution-linked commission details with explicit coverage and signed rebates."""

from typing import Any

from .account_insights import AccountInsights, InsightRow, insight_check, insight_date, insight_decimal, insight_label, insight_sum

_COMPONENTS = ("brokerExecutionCharge", "brokerClearingCharge", "thirdPartyExecutionCharge",
               "thirdPartyClearingCharge", "thirdPartyRegulatoryCharge", "other")
_REGULATORY = ("regSection31TransactionFee", "regFINRATradingActivityFee", "regOther")


def account_commissions(rows: list[InsightRow], trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare detail totals without adding nested regulatory charges twice."""
    source = AccountInsights(rows, [])
    details = [r for r in source.section("UnbundledCommissionDetails") if r.payload.get("conid")]
    result = []
    linked: set[str] = set()
    for row in details:
        p = row.payload
        trade_id = str(p.get("tradeID") or "")
        currency = str(p.get("currency") or "").strip().upper()
        candidates = [t for t in trades if trade_id and str(t["payload"].get("tradeID")) == trade_id
                      and str(t["payload"].get("conid")) == str(p["conid"])
                      and str(t["payload"].get("ibCommissionCurrency") or t["currency"]).strip().upper() == currency]
        duplicate = sum(str(r.payload.get("tradeID") or "") == trade_id and
                        str(r.payload.get("conid")) == str(p["conid"]) and r.payload.get("currency") == currency
                        for r in details) != 1
        trade = candidates[0] if len(candidates) == 1 and not duplicate else None
        if trade:
            linked.add(trade["event_id"])
        components: list[dict[str, Any]] = [{"field": key, "label": insight_label(key), "amount": insight_decimal(p.get(key))}
                      for key in _COMPONENTS]
        total = insight_decimal(p.get("totalCommission"))
        result.append({"symbol": p.get("symbol"), "trade_id": trade_id, "date": insight_date(p.get("dateTime")),
                       "currency": currency, "total": total, "components": components, "raw_id": row.raw_id,
                       "instrument_id": trade["instrument_id"] if trade else None,
                       "duplicate": duplicate,
                       "components_check": insight_check("Commission component sum", total,
                            insight_sum([r["amount"] for r in components]),
                            "Regulatory detail is already included in the third-party regulatory subtotal."),
                       "execution_check": insight_check("Commission versus execution", total,
                            trade["commission"] if trade else None,
                            "Signed broker amounts: negative charges, positive rebates; unique trade and commission currency required."),
                       "regulatory": [{"field": k, "label": insight_label(k), "amount": insight_decimal(p.get(k))} for k in _REGULATORY]})
    selected = source.section("UnbundledCommissionDetails")
    cash = [r for r in source.section("CashReport") if selected and r.artifact_id == selected[0].artifact_id]
    period = cash[0].payload if cash else {}
    start, end = insight_date(period.get("fromDate")), insight_date(period.get("toDate"))
    eligible = [t for t in trades if start and end and start <= t["report_date_local"] <= end
                and t["commission"] is not None and t["commission"] != 0]
    currencies = sorted({r["currency"] for r in result})
    totals = [{"currency": currency,
               "total": insight_sum([None if r["duplicate"] else r["total"] for r in result if r["currency"] == currency]),
               "components": [{"label": insight_label(key), "amount": insight_sum(
                   [None if r["duplicate"] else next(c["amount"] for c in r["components"] if c["field"] == key)
                    for r in result if r["currency"] == currency])} for key in _COMPONENTS]}
              for currency in currencies]
    return {"details": result, "totals": totals, "from_date": start, "to_date": end,
            "covered_executions": len({t["event_id"] for t in eligible} & linked) if start and end else None,
            "commissioned_executions": len(eligible) if start and end else None,
            "missing_detail_executions": len({t["event_id"] for t in eligible} - linked) if start and end else None}
