"""Shared selection of ordered, positive fallback conversion rates."""

from __future__ import annotations

from bisect import bisect_right
from datetime import date
from decimal import Decimal
from operator import attrgetter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.interfaces import LedgerFxRateRecord


class ConversionRateHistory:
    """Find the last eligible rate in the ledger repository's deterministic ordering."""

    def __init__(self, rates: list[LedgerFxRateRecord]) -> None:
        self._by_pair: dict[tuple[str, str], list[LedgerFxRateRecord]] = {}
        for row in reversed(rates):
            if row.fx_rate is None:
                continue
            rate = Decimal(row.fx_rate)
            if not rate.is_finite() or rate <= 0:
                continue
            pair = row.currency.strip().upper(), row.functional_currency.strip().upper()
            history = self._by_pair.setdefault(pair, [])
            # A later row with an equal or earlier date always wins when both are eligible.
            if not history or row.report_date_local < history[-1].report_date_local:
                history.append(row)
        for history in self._by_pair.values():
            history.reverse()

    def select(
        self, currency: str, functional_currency: str, report_date_local: date,
    ) -> LedgerFxRateRecord | None:
        pair = currency.strip().upper(), functional_currency.strip().upper()
        history = self._by_pair.get(pair)
        if history is None:
            return None
        index = bisect_right(history, report_date_local, key=attrgetter("report_date_local"))
        return history[index - 1] if index else None
