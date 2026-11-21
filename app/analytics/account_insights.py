"""Read-only account value analysis from successfully published Flex sections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import cached_property
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


@dataclass(frozen=True)
class OpenPositionEvidence:
    """Keep all reported rows for one security and require a unique summary."""

    rows: list[InsightRow]
    summaries: list[InsightRow]

    @property
    def summary(self) -> InsightRow | None:
        return self.summaries[0] if len(self.summaries) == 1 else None


def position_fx_rate(payload: dict[str, Any]) -> Decimal | None:
    """Read the positive rate into the account's USD reporting currency."""
    if str(payload.get("currency") or "").strip().upper() == "USD":
        return Decimal("1")
    rate = insight_decimal(payload.get("fxRateToBase"))
    return rate if rate is not None and rate > 0 else None


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


def _dated_equity_rows(rows: list[InsightRow]) -> dict[date, InsightRow | None]:
    """Keep each date's first artifact and reject conflicts within that artifact."""
    by_date: dict[date, InsightRow] = {}
    conflicts: set[date] = set()
    for row in rows:
        day = insight_date(row.payload.get("reportDate"))
        if day is None:
            continue
        first = by_date.setdefault(day, row)
        if first is row or first.artifact_id != row.artifact_id:
            continue
        if {**first.payload, "reportDate": day} != {**row.payload, "reportDate": day}:
            conflicts.add(day)
    return {day: None if day in conflicts else row for day, row in by_date.items()}


class AccountInsights:
    """Build dated account reports without modifying the financial ledger."""

    def __init__(self, rows: list[InsightRow], history: list[InsightRow]) -> None:
        self.rows = rows
        self.history = history

    def section(self, name: str) -> list[InsightRow]:
        """Return one whole selected section, including its empty marker."""
        return [row for row in self.rows if row.section == name]

    @cached_property
    def open_positions(self) -> dict[str, OpenPositionEvidence]:
        """Index securities in the selected OpenPositions section, including detail-only rows."""
        by_conid: dict[str, list[InsightRow]] = {}
        for row in self.section("OpenPositions"):
            conid = str(row.payload.get("conid") or "").strip()
            category = str(row.payload.get("assetCategory") or "").strip().upper()
            if conid and category not in ("CASH", "FX"):
                by_conid.setdefault(conid, []).append(row)
        return {
            conid: OpenPositionEvidence(rows, [
                row for row in rows
                if (str(row.payload.get("levelOfDetail") or "").strip().upper() or "SUMMARY") == "SUMMARY"
            ])
            for conid, rows in by_conid.items()
        }

    @cached_property
    def cash_report_periods(self) -> dict[str, tuple[date, date]]:
        """Find explicit, agreeing CashReport periods for each artifact."""
        periods: dict[str, set[tuple[date, date] | None]] = {}
        for row in self.section("CashReport"):
            from_date, to_date = row.payload.get("fromDate"), row.payload.get("toDate")
            if not from_date and not to_date:
                continue
            start, end = insight_date(from_date), insight_date(to_date)
            period = (start, end) if start is not None and end is not None and start <= end else None
            periods.setdefault(row.artifact_id, set()).add(period)
        return {
            artifact_id: period
            for artifact_id, evidence in periods.items()
            if len(evidence) == 1 and (period := next(iter(evidence))) is not None
        }

    def build(self) -> dict[str, Any]:
        """Return NAV, history and period changes with explicit source context."""
        sections = {
            name: {"report_date": rows[0].report_date, "artifact_id": rows[0].artifact_id,
                   "raw_ids": [row.raw_id for row in rows]}
            for name in sorted({row.section for row in self.rows})
            if (rows := self.section(name))
        }
        history = [
            {"date": day, "nav": insight_decimal(row.payload.get("total")) if row else None,
             "currency": row.payload.get("currency") if row else None, "raw_id": row.raw_id if row else None}
            for day, row in sorted(_dated_equity_rows(self.history).items())
        ]
        return {"sections": sections, "nav": self.nav(), "nav_history": history,
                "change_in_nav": self.change_in_nav()}

    def nav(self) -> dict[str, Any] | None:
        """Compare same-date broker NAV with cash, positions and other NAV components."""
        equity = _dated_equity_rows(self.section("EquitySummaryInBase"))
        if not equity:
            return None
        day = max(equity)
        row = equity[day]
        payload = row.payload if row else {}
        currency = payload.get("currency")
        components: list[dict[str, Any]] = [{"field": key, "label": insight_label(key), "amount": insight_decimal(payload[key])}
                      for key in _NAV_COMPONENTS if key in payload]
        missing = [key for key in _NAV_COMPONENTS if insight_decimal(payload.get(key)) is None]
        cash_rows = self.section("CashReport")
        cash = [item for item in cash_rows
                if str(item.payload.get("currency") or "").strip().upper() == "BASE_SUMMARY"]
        positions = self.section("OpenPositions")
        valid_dates = (currency == "USD" and len(cash) == 1 and
                       cash[0].report_date == day and bool(positions) and positions[0].report_date == day)
        amounts: list[Decimal | None] = []
        missing_summaries = []
        for conid, position in self.open_positions.items():
            summary = position.summary
            if summary is None:
                missing_summaries.append(conid)
                continue
            value = insight_decimal(summary.payload.get("positionValue"))
            rate = position_fx_rate(summary.payload)
            amounts.append(value * rate if value is not None and rate is not None else None)
        estimate = (
            insight_sum([insight_decimal(cash[0].payload.get("endingCash")), insight_sum(amounts)])
            if valid_dates and not missing_summaries else None
        )
        extras = insight_sum([entry["amount"] for entry in components if entry["field"] not in ("cash", "stock", "options", "bonds", "funds", "notes", "commodities", "crypto", "physDel")])
        explained = insight_sum([estimate, extras]) if not missing else None
        return {"date": day, "currency": currency, "total": insight_decimal(payload.get("total")),
                "raw_id": row.raw_id if row else None, "components": components, "cash_and_positions": estimate,
                "additional_components": extras,
                "check": insight_check("Account value including accruals and other components",
                                       insight_decimal(payload.get("total")), explained,
                                       "Conflicting broker NAV rows for this date." if row is None else
                                       "Missing unique position summaries for conids: " + ", ".join(sorted(missing_summaries))
                                       if missing_summaries else "Missing NAV components: " + ", ".join(missing) if missing else
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
        missing = [key for key in fields if insight_decimal(p.get(key)) is None]
        complete = not ambiguous and not unknown and not missing
        calculated = insight_sum([starting, *[item["amount"] for item in components]]) if complete else None
        return {"from_date": insight_date(p.get("fromDate")), "to_date": insight_date(p.get("toDate")),
                "currency": p.get("currency"), "starting": starting, "ending": ending,
                "twr_percent": insight_decimal(p.get("twr")), "components": components, "raw_id": row.raw_id,
                "unknown_fields": unknown, "check": insight_check("Period NAV movement", ending, calculated,
                    "Broker component arithmetic; MTM and realized modes are alternatives. Unknown fields: " + ", ".join(unknown)
                    if unknown else "Missing NAV movements: " + ", ".join(missing) if missing
                    else "Broker component arithmetic; not an independent ledger check." if not ambiguous
                    else "Ambiguous MTM and realized components.")}
