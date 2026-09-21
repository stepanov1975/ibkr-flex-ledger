"""Read-only account value analysis from successfully published Flex sections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from app.domain.flex_parsing import domain_flex_normalize_optional_text, domain_flex_parse_local_date


@dataclass(frozen=True)
class InsightRow:
    """A broker row with its immutable source and statement date."""

    section: str
    payload: dict[str, Any]
    raw_id: str
    artifact_id: str
    report_date: date


def insight_decimal(value: object) -> Decimal | None:
    """Parse optional broker numbers; missing and non-finite values stay unknown."""
    normalized = domain_flex_normalize_optional_text(str(value)) if value is not None else None
    if normalized is None:
        return None
    try:
        result = Decimal(normalized.replace(",", ""))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def insight_date(value: object) -> date | None:
    """Normalize optional Flex business dates."""
    return domain_flex_parse_local_date(str(value)) if value else None


def insight_sum(values: list[Decimal | None]) -> Decimal | None:
    """Sum only complete inputs, preserving known empty totals as zero."""
    return None if any(value is None for value in values) else sum(
        (value for value in values if value is not None), Decimal("0"),
    )


def insight_check(name: str, broker: Decimal | None, calculated: Decimal | None,
                  reason: str = "", tolerance: Decimal = Decimal("0.01")) -> dict[str, Any]:
    """Describe a comparison without presenting missing evidence as a pass."""
    difference = None if broker is None or calculated is None else calculated - broker
    return {"name": name, "broker": broker, "calculated": calculated, "difference": difference,
            "status": "not_comparable" if difference is None else
            ("matched" if abs(difference) <= tolerance else "different"), "reason": reason}


_NAV_COMPONENTS = (
    "cash", "stock", "options", "bonds", "funds", "notes", "commodities", "crypto", "physDel",
    "cfdUnrealizedPl", "forexCfdUnrealizedPl", "dividendAccruals", "interestAccruals",
    "slbCashCollateral", "slbDirectSecuritiesBorrowed", "slbDirectSecuritiesLent",
    "softDollars", "ipoSubscription", "incentiveCouponAccruals", "cgtWithholdingAccruals",
    "liteSurchargeAccruals", "marginFinancingChargeAccruals", "eventContractInterestAccruals",
)
_CHANGE_COMPONENTS = (
    "costAdjustments", "transferredPnlAdjustments", "depositsWithdrawals", "internalCashTransfers",
    "assetTransfers", "debitCardActivity", "billPay", "dividends", "withholdingTax", "withholding871m",
    "withholdingTaxCollected", "changeInDividendAccruals", "interest", "changeInInterestAccruals",
    "advisorFees", "brokerFees", "changeInBrokerFeeAccruals", "clientFees", "otherFees", "feesReceivables",
    "commissions", "commissionReceivables", "forexCommissions", "transactionTax", "taxReceivables",
    "salesTax", "softDollars", "netFxTrading", "fxTranslation", "linkingAdjustments", "other",
    "corporateActionProceeds", "commissionCreditsRedemption", "grantActivity", "excessFundSweep",
    "billableSalesTax", "mtmAtPaxos", "carbonCredits", "donations", "paxosTransfers", "commissionsAtPaxos",
    "referralFee", "changeInIncentiveCouponAccruals", "otherIncome", "changeInLiteSurchargeAccruals",
    "changeInCGTWithholdingAccruals",
)


def insight_label(field: str) -> str:
    """Render broker component names as readable labels."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", field).capitalize()


class AccountInsights:
    """Build dated account reports without modifying the financial ledger."""

    def __init__(self, rows: list[InsightRow], history: list[InsightRow]) -> None:
        self.rows = rows
        self.history = history

    def section(self, name: str) -> list[InsightRow]:
        """Return one whole selected section, including its empty marker."""
        return [row for row in self.rows if row.section == name]

    def build(self) -> dict[str, Any]:
        """Return NAV, history and period changes with explicit source context."""
        sections = {
            name: {"report_date": rows[0].report_date, "artifact_id": rows[0].artifact_id,
                   "raw_ids": [row.raw_id for row in rows]}
            for name in sorted({row.section for row in self.rows})
            if (rows := self.section(name))
        }
        history_by_date: dict[date, dict[str, Any]] = {}
        for row in self.history:
            day = insight_date(row.payload.get("reportDate"))
            if day:
                history_by_date.setdefault(day, {"date": day, "nav": insight_decimal(row.payload.get("total")),
                                                "currency": row.payload.get("currency"), "raw_id": row.raw_id})
        history = [history_by_date[day] for day in sorted(history_by_date)]
        return {"sections": sections, "nav": self.nav(), "nav_history": history,
                "change_in_nav": self.change_in_nav()}

    def nav(self) -> dict[str, Any] | None:
        """Compare same-date broker NAV with cash, positions and other NAV components."""
        equity = self.section("EquitySummaryInBase")
        if not equity:
            return None
        row = max(equity, key=lambda item: insight_date(item.payload.get("reportDate")) or date.min)
        payload = row.payload
        day = insight_date(payload.get("reportDate"))
        currency = payload.get("currency")
        components: list[dict[str, Any]] = [{"field": key, "label": insight_label(key), "amount": insight_decimal(payload[key])}
                      for key in _NAV_COMPONENTS if key in payload]
        cash_rows = self.section("CashReport")
        cash = [item for item in cash_rows if item.payload.get("currency") == "BASE_SUMMARY"]
        positions = self.section("OpenPositions")
        valid_dates = (day is not None and currency == "USD" and len(cash) == 1 and
                       cash[0].report_date == day and bool(positions) and positions[0].report_date == day)
        amounts: list[Decimal | None] = []
        seen: set[str] = set()
        for item in positions:
            p = item.payload
            if not p.get("conid") or p.get("assetCategory") in ("CASH", "FX"):
                continue
            conid = str(p["conid"])
            if p.get("levelOfDetail", "SUMMARY") != "SUMMARY":
                continue
            if conid in seen:
                valid_dates = False
            seen.add(conid)
            value, rate = insight_decimal(p.get("positionValue")), insight_decimal(p.get("fxRateToBase"))
            if p.get("currency") == currency:
                rate = Decimal("1")
            amounts.append(value * rate if value is not None and rate is not None and rate > 0 else None)
        if any(item.payload.get("conid") for item in positions) and not seen:
            valid_dates = False
        estimate = insight_sum([insight_decimal(cash[0].payload.get("endingCash")), insight_sum(amounts)]) if valid_dates else None
        extras = insight_sum([entry["amount"] for entry in components if entry["field"] not in ("cash", "stock", "options", "bonds", "funds", "notes", "commodities", "crypto", "physDel")])
        explained = insight_sum([estimate, extras])
        return {"date": day, "currency": currency, "total": insight_decimal(payload.get("total")),
                "raw_id": row.raw_id, "components": components, "cash_and_positions": estimate,
                "additional_components": extras,
                "check": insight_check("Account value including accruals and other components",
                                       insight_decimal(payload.get("total")), explained,
                                       "Same-date cash and positions plus reported additional NAV components; missing inputs remain unknown.")}

    def change_in_nav(self) -> dict[str, Any] | None:
        """Explain period NAV changes, keeping MTM and realized modes alternative."""
        rows = [row for row in self.section("ChangeInNAV") if row.payload.get("startingValue") is not None]
        if len(rows) != 1:
            return None
        row = rows[0]
        p = row.payload
        mtm = insight_decimal(p.get("mtm"))
        realized = insight_decimal(p.get("realized"))
        unrealized = insight_decimal(p.get("changeInUnrealized"))
        ambiguous = mtm not in (None, Decimal("0")) and any(
            value not in (None, Decimal("0")) for value in (realized, unrealized))
        mode_fields = ("mtm",) if mtm not in (None, Decimal("0")) or (realized is None and unrealized is None) else ("realized", "changeInUnrealized")
        fields = (*mode_fields, *_CHANGE_COMPONENTS)
        components: list[dict[str, Any]] = [{"field": key, "label": insight_label(key), "amount": insight_decimal(p[key])}
                      for key in fields if key in p]
        known = {*fields, "mtm", "realized", "changeInUnrealized", "accountId", "acctAlias", "model",
                 "currency", "fromDate", "toDate", "startingValue", "endingValue", "twr"}
        unknown = sorted(key for key, value in p.items() if key not in known and str(value).strip())
        starting, ending = insight_decimal(p.get("startingValue")), insight_decimal(p.get("endingValue"))
        complete = not ambiguous and not unknown and all(insight_decimal(p.get(key)) is not None for key in mode_fields)
        calculated = insight_sum([starting, *[item["amount"] for item in components]]) if complete else None
        return {"from_date": insight_date(p.get("fromDate")), "to_date": insight_date(p.get("toDate")),
                "currency": p.get("currency"), "starting": starting, "ending": ending,
                "twr_percent": insight_decimal(p.get("twr")), "components": components, "raw_id": row.raw_id,
                "unknown_fields": unknown, "check": insight_check("Period NAV movement", ending, calculated,
                    "Broker component arithmetic; MTM and realized modes are alternatives. Unknown fields: " + ", ".join(unknown)
                    if unknown else "Broker component arithmetic; not an independent ledger check." if not ambiguous
                    else "Ambiguous MTM and realized components.")}
