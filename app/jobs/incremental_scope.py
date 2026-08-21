"""Derive the immutable scope for an incremental snapshot rebuild."""

from dataclasses import dataclass

from app.db.interfaces import RawRecordForCanonicalMapping


@dataclass(frozen=True)
class IncrementalSnapshotScope:
    """Conids and source currencies affected by changed raw rows."""

    conids: frozenset[str]
    currencies: frozenset[str]
    full_rebuild_reason: str | None


def job_build_incremental_snapshot_scope(
    rows: list[RawRecordForCanonicalMapping],
) -> IncrementalSnapshotScope:
    """Build the narrowest safe snapshot scope from changed raw rows."""

    conids: set[str] = set()
    currencies: set[str] = set()
    for row in rows:
        if row.section_name in {"Trades", "CashTransactions", "CorporateActions", "OpenPositions"}:
            raw_conid = row.source_payload.get("conid")
            conid = raw_conid.strip() if isinstance(raw_conid, str) else ""
            if not conid:
                return IncrementalSnapshotScope(
                    frozenset(),
                    frozenset(),
                    f"unscopable_changed_row:{row.section_name}:missing_conid",
                )
            conids.add(conid)
        elif row.section_name == "ConversionRates":
            raw_currency = row.source_payload.get("fromCurrency")
            currency = raw_currency.strip().upper() if isinstance(raw_currency, str) else ""
            if not currency:
                return IncrementalSnapshotScope(
                    frozenset(),
                    frozenset(),
                    "unscopable_changed_row:ConversionRates:missing_fromCurrency",
                )
            currencies.add(currency)
    return IncrementalSnapshotScope(frozenset(conids), frozenset(currencies), None)
