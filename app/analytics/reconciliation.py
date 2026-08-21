"""Broker-versus-economic reconciliation using the frozen tolerance matrix."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.db import ReconciliationSourceRecord


@dataclass(frozen=True)
class ReconciliationDiff:
    report_date_local: date
    instrument_id: UUID
    conid: str
    symbol: str
    metric: str
    broker_value: Decimal | None
    economic_value: Decimal | None
    abs_diff: Decimal
    rel_diff: Decimal | None
    tolerance_abs: Decimal
    tolerance_rel: Decimal
    within_tolerance: bool
    formula_context: str
    source_event_id: UUID | None
    source_raw_record_id: UUID | None
    provisional: bool


_CURRENCY_MINOR_UNIT = {"USD": Decimal("0.01"), "EUR": Decimal("0.01"), "ILS": Decimal("0.01"), "JPY": Decimal("1")}
_MONEY_METRICS = ("realized_pnl", "unrealized_pnl", "fees", "withholding_tax")
_RELATIVE_TOLERANCE = Decimal("0.0001")
_POSITION_TOLERANCE = Decimal("0.000001")


def analytics_build_reconciliation_diffs(source_rows: list[ReconciliationSourceRecord]) -> list[ReconciliationDiff]:
    """Expand per-instrument source rows into deterministic per-metric diffs."""

    diffs: list[ReconciliationDiff] = []
    for row in source_rows:
        for metric in ("position_qty", *_MONEY_METRICS):
            economic_value = Decimal(getattr(row, metric))
            broker_raw = getattr(row, f"broker_{metric}")
            broker_value = None if broker_raw is None else Decimal(broker_raw)
            if metric == "position_qty":
                tolerance_abs = _POSITION_TOLERANCE
                tolerance_rel = Decimal("0")
                formula = "abs(a-b) <= 0.000001"
            else:
                tolerance_abs = max(Decimal("0.01"), _CURRENCY_MINOR_UNIT.get(row.currency, Decimal("0.01")))
                tolerance_rel = _RELATIVE_TOLERANCE
                formula = "abs(a-b) <= abs_tol OR abs(a-b)/max(abs(b),1e-9) <= rel_tol"

            if broker_value is None:
                abs_diff = abs(economic_value)
                rel_diff = None
                within_tolerance = False
            else:
                abs_diff = abs(broker_value - economic_value)
                denominator = max(abs(broker_value), Decimal("0.000000001"))
                rel_diff = abs_diff / denominator if tolerance_rel else None
                within_tolerance = abs_diff <= tolerance_abs or (
                    tolerance_rel > Decimal("0") and rel_diff is not None and rel_diff <= tolerance_rel
                )

            diffs.append(
                ReconciliationDiff(
                    report_date_local=row.report_date_local,
                    instrument_id=row.instrument_id,
                    conid=row.conid,
                    symbol=row.symbol,
                    metric=metric,
                    broker_value=broker_value,
                    economic_value=economic_value,
                    abs_diff=abs_diff,
                    rel_diff=rel_diff,
                    tolerance_abs=tolerance_abs,
                    tolerance_rel=tolerance_rel,
                    within_tolerance=within_tolerance,
                    formula_context=formula,
                    source_event_id=row.source_event_id,
                    source_raw_record_id=row.source_raw_record_id,
                    provisional=row.provisional or broker_value is None,
                )
            )
    return diffs
