"""Resolve replay overrides with the same natural identities as canonical mapping."""

from dataclasses import dataclass

from app.db import RawRecordForCanonicalMapping
from app.mapping import RawRecordForMapping
from app.mapping.service import CanonicalMappingService


@dataclass(frozen=True)
class ReplayEventSources:
    """First and latest available successful applications of each canonical event."""

    first: dict[tuple[str | None, ...], RawRecordForCanonicalMapping]
    latest: dict[tuple[str | None, ...], RawRecordForCanonicalMapping]


def job_replay_event_sources(
    account_id: str,
    functional_currency: str,
    raw_records: list[RawRecordForCanonicalMapping],
) -> ReplayEventSources:
    """Index ordered applications by mapped key, retaining first and latest sources.

    Raw row references cannot identify canonical versions: lot references can
    repeat, execution identity has fallbacks, and synthetic FX keys include dates.
    Mapping supplies the normalized identities used by canonical persistence.
    """

    batch = CanonicalMappingService().mapping_build_canonical_batch(
        account_id=account_id,
        functional_currency=functional_currency,
        raw_records=[RawRecordForMapping(
            raw_record_id=row.raw_record_id,
            ingestion_run_id=row.ingestion_run_id,
            section_name=row.section_name,
            source_row_ref=row.source_row_ref,
            report_date_local=row.report_date_local,
            source_payload=row.source_payload,
        ) for row in raw_records],
    )
    source_ids: dict[tuple[str | None, ...], str] = {}
    first_source_ids: dict[tuple[str | None, ...], str] = {}

    def record_source(key: tuple[str | None, ...], source_id: str) -> None:
        first_source_ids.setdefault(key, source_id)
        source_ids[key] = source_id

    for trade in batch.trade_fill_requests:
        record_source(("trade", trade.ib_exec_id.strip()), trade.source_raw_record_id)
    for cash in batch.cashflow_requests:
        record_source(("cash", cash.transaction_id, cash.cash_action, cash.currency), cash.source_raw_record_id)
    for fx in batch.fx_requests:
        record_source(("fx", fx.transaction_id, fx.currency, fx.functional_currency), fx.source_raw_record_id)
    for action in batch.corp_action_requests:
        key = (("action", action.action_id) if action.action_id is not None else
               ("action_fallback", action.transaction_id, action.conid, action.report_date_local, action.reorg_code))
        record_source(key, action.source_raw_record_id)
    rows_by_id = {str(row.raw_record_id): row for row in raw_records}
    return ReplayEventSources(
        first={key: rows_by_id[source_id] for key, source_id in first_source_ids.items()},
        latest={key: rows_by_id[source_id] for key, source_id in source_ids.items()},
    )
