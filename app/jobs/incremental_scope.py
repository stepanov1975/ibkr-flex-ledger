"""Derive the immutable scope for an incremental snapshot rebuild."""

from app.db.interfaces import RawRecordForCanonicalMapping
from app.ledger import SnapshotRebuildScope


def job_build_incremental_snapshot_scope(
    rows: list[RawRecordForCanonicalMapping],
) -> SnapshotRebuildScope:
    """Interpret changed raw rows as security and currency rebuild hints."""

    conids: set[str] = set()
    currencies: set[str] = set()
    for row in rows:
        if row.section_name in {"Trades", "CashTransactions", "CorporateActions", "OpenPositions"}:
            raw_conid = row.source_payload.get("conid")
            conid = raw_conid.strip() if isinstance(raw_conid, str) else ""
            if not conid:
                return SnapshotRebuildScope(
                    frozenset(),
                    frozenset(),
                    f"unscopable_changed_row:{row.section_name}:missing_conid",
                )
            conids.add(conid)
        elif row.section_name == "ConversionRates":
            raw_currency = row.source_payload.get("fromCurrency")
            currency = raw_currency.strip().upper() if isinstance(raw_currency, str) else ""
            if not currency:
                return SnapshotRebuildScope(
                    frozenset(),
                    frozenset(),
                    "unscopable_changed_row:ConversionRates:missing_fromCurrency",
                )
            currencies.add(currency)
    return SnapshotRebuildScope(frozenset(conids), frozenset(currencies), None)
