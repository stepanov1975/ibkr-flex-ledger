"""Pending broker income and conservative action-linked payment evidence."""

from decimal import Decimal
from typing import Any

from .account_insights import AccountInsights, InsightRow, insight_check, insight_date, insight_decimal, insight_sum


def account_income(rows: list[InsightRow], history: list[InsightRow], cash: list[dict[str, Any]]) -> dict[str, Any]:
    """Separate pending accrual snapshots from booked payments and reversals."""
    source = AccountInsights(rows, [])
    pending = [_dividend(row) for row in source.section("OpenDividendAccruals") if row.payload.get("conid")]
    currencies = sorted({r["currency"] for r in pending if r["currency"]})
    totals = [{"currency": currency, "net": insight_sum([r["net"] for r in pending if r["currency"] == currency])}
              for currency in currencies]
    latest: dict[tuple[str, str, str, str], list[InsightRow]] = {}
    for row in history:
        p = row.payload
        if p.get("conid"):
            key = (str(p.get("actionID") or ""), str(p["conid"]), str(p.get("currency", "")),
                   str(p.get("payDate") or "") if not p.get("actionID") else "")
            group = latest.setdefault(key, [])
            if not group or group[0].artifact_id == row.artifact_id:
                if all(previous.raw_id != row.raw_id for previous in group):
                    group.append(row)
    settlements = []
    for (action, conid, currency, _), group in latest.items():
        row = group[0]
        candidates = [e for e in cash if action and str(e["payload"].get("actionID")) == action
                      and str(e["payload"].get("conid")) == conid and e["currency"] == currency]
        payments = [e for e in candidates if e["cash_action"].lower() in ("dividends", "payment in lieu of dividends")]
        taxes = [e for e in candidates if e["cash_action"].lower() == "withholding tax"]
        unexpected = [e for e in candidates if e not in payments and e not in taxes]
        reversed_accrual = any(str(r.payload.get("actionID")) == action and str(r.payload.get("conid")) == conid
                               and r.payload.get("currency") == currency and r.payload.get("code") == "Re"
                               for r in source.section("ChangeInDividendAccruals"))
        expected = insight_decimal(row.payload.get("netAmount"))
        actual = sum((e["amount"] - (e.get("fees") or Decimal("0")) - (e.get("withholding_tax") or Decimal("0"))
                      for e in payments + taxes), Decimal("0")) if payments and not unexpected else None
        fee = insight_decimal(row.payload.get("fee"))
        if fee not in (None, Decimal("0")):
            actual = None
        reason = ("Action/security/currency-linked booked payments and withholding; accrual reversals are not payments."
                  if payments else "No uniquely action-linked payment found; a reversal alone does not prove payment.")
        if unexpected or fee not in (None, Decimal("0")):
            reason = "Additional fees or unclassified linked cashflows require review."
        if len(group) != 1 or not action:
            actual = None
            expected = None if len(group) != 1 else expected
            reason = "Multiple entitlement rows or missing action identity; payment match is unverified."
        settlements.append({**_dividend(row), "net": expected, "reversal_seen": reversed_accrual,
                            "payment_raw_ids": [e["raw_id"] for e in candidates],
                            "check": insight_check("Accrual versus paid net income", expected, actual, reason)})
    interest = []
    for row in source.section("InterestAccruals"):
        p = row.payload
        if not p.get("currency"):
            continue
        starting, ending = insight_decimal(p.get("startingAccrualBalance")), insight_decimal(p.get("endingAccrualBalance"))
        interest.append({"currency": "USD" if p["currency"] == "BASE_SUMMARY" else p["currency"],
                         "is_base_summary": p["currency"] == "BASE_SUMMARY",
                         "from_date": insight_date(p.get("fromDate")), "to_date": insight_date(p.get("toDate")),
                         "starting": starting, "accrued": insight_decimal(p.get("interestAccrued")),
                         "reversal": insight_decimal(p.get("accrualReversal")), "ending": ending, "raw_id": row.raw_id,
                         "check": insight_check("Interest accrual rollforward", ending,
                             insight_sum([starting, *[insight_decimal(p.get(k)) for k in
                                                     ("interestAccrued", "accrualReversal", "fxTranslation")]]),
                             "Broker accrual arithmetic; ending accrual is not paid cash.")})
    return {"pending_dividends": pending, "pending_totals": totals,
            "payment_checks": settlements, "interest": interest}


def _dividend(row: InsightRow) -> dict[str, Any]:
    """Normalize one accrual while retaining its broker entitlement and dates."""
    p = row.payload
    gross, tax, fee, net = [insight_decimal(p.get(k)) for k in ("grossAmount", "tax", "fee", "netAmount")]
    calculated = gross - tax - fee if gross is not None and tax is not None and fee is not None else None
    return {"symbol": p.get("symbol"), "conid": p.get("conid"), "currency": p.get("currency"),
            "ex_date": insight_date(p.get("exDate")), "pay_date": insight_date(p.get("payDate")),
            "report_date": row.report_date, "quantity": insight_decimal(p.get("quantity")),
            "gross": gross, "tax": tax, "fee": fee, "net": net, "raw_id": row.raw_id,
            "amount_check": insight_check("Dividend gross less tax and fees", net, calculated)}
