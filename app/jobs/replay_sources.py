"""Resolve replay overrides with the same natural identities as canonical mapping."""

from app.db import RawRecordForCanonicalMapping
from app.mapping import RawRecordForMapping
from app.mapping.service import CanonicalMappingService


def job_replay_event_sources(
    account_id: str,
    functional_currency: str,
    raw_records: list[RawRecordForCanonicalMapping],
) -> dict[tuple[str | None, ...], RawRecordForCanonicalMapping]:
    """Index ordered raw versions by mapped event key, retaining the latest version.

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
    for trade in batch.trade_fill_requests:
        source_ids[("trade", trade.ib_exec_id.strip())] = trade.source_raw_record_id
    for cash in batch.cashflow_requests:
        source_ids[("cash", cash.transaction_id, cash.cash_action, cash.currency)] = cash.source_raw_record_id
    for fx in batch.fx_requests:
        source_ids[("fx", fx.transaction_id, fx.currency, fx.functional_currency)] = fx.source_raw_record_id
    for action in batch.corp_action_requests:
        key = (("action", action.action_id) if action.action_id is not None else
               ("action_fallback", action.transaction_id, action.conid, action.report_date_local, action.reorg_code))
        source_ids[key] = action.source_raw_record_id
    rows_by_id = {str(row.raw_record_id): row for row in raw_records}
    return {key: rows_by_id[source_id] for key, source_id in source_ids.items()}
