"""Shared selection of ordered, positive fallback conversion rates."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.interfaces import LedgerFxRateRecord


def select_conversion_rate(
    currency: str, functional_currency: str, report_date_local: date, rates: list[LedgerFxRateRecord],
) -> LedgerFxRateRecord | None:
    """Use the last eligible row in the ledger repository's deterministic ordering."""
    return next((row for row in reversed(rates)
                 if row.currency.strip().upper() == currency.strip().upper()
                 and row.functional_currency.strip().upper() == functional_currency.strip().upper()
                 and row.report_date_local <= report_date_local
                 and row.fx_rate is not None and Decimal(row.fx_rate) > 0), None)
