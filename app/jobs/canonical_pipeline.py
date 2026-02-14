"""Shared canonical mapping pipeline helpers for ingestion and reprocess workflows."""

from __future__ import annotations

from dataclasses import replace

from app.db import (
    CanonicalInstrumentRecord,
    CanonicalInstrumentUpsertRequest,
    CanonicalPersistenceRepositoryPort,
    CanonicalTradeFillUpsertRequest,
    RawRecordForCanonicalMapping,
)
from app.mapping import CanonicalMappingBatch, MappingContractViolationError, RawRecordForMapping
from app.mapping.service import CanonicalMappingService


def job_canonical_map_and_persist(
    account_id: str,
    functional_currency: str,
    raw_records: list[RawRecordForCanonicalMapping],
    canonical_persistence_repository: CanonicalPersistenceRepositoryPort,
    mapping_service: CanonicalMappingService | None = None,
) -> dict[str, int]:
    """Map raw rows into canonical contracts and persist with deterministic UPSERT logic.

    Args:
        account_id: Internal account context identifier.
        functional_currency: Functional/base reporting currency code.
        raw_records: Raw rows to process.
        canonical_persistence_repository: Canonical persistence repository.
        mapping_service: Optional mapping service override.

    Returns:
        dict[str, int]: Persisted canonical row counters by event type.

    Raises:
        MappingContractViolationError: Raised when one raw row violates required mapping contract.
        RuntimeError: Raised when persistence operations fail.
        ValueError: Raised when top-level input values are invalid.
    """

    service = mapping_service or CanonicalMappingService()
    mapping_input_rows = [
        RawRecordForMapping(
            raw_record_id=row.raw_record_id,
            ingestion_run_id=row.ingestion_run_id,
            section_name=row.section_name,
            source_row_ref=row.source_row_ref,
            report_date_local=row.report_date_local,
            source_payload=row.source_payload,
        )
        for row in raw_records
    ]
    mapped_batch = service.mapping_build_canonical_batch(
        account_id=account_id,
        functional_currency=functional_currency,
        raw_records=mapping_input_rows,
    )

    instrument_id_by_conid = _job_canonical_upsert_instruments(
        mapped_batch=mapped_batch,
        canonical_persistence_repository=canonical_persistence_repository,
    )
    conid_by_raw_record_id = _job_canonical_build_conid_index(raw_records=raw_records)

    resolved_trade_requests: list[CanonicalTradeFillUpsertRequest] = []
    for trade_request in mapped_batch.trade_fill_requests:
        conid = conid_by_raw_record_id.get(trade_request.source_raw_record_id)
        if conid is None:
            raise MappingContractViolationError(
                "mapping contract violation: trade row missing conid context "
                f"for source_raw_record_id={trade_request.source_raw_record_id}"
            )

        instrument_record = instrument_id_by_conid.get(conid)
        if instrument_record is None:
            raise MappingContractViolationError(
                "mapping contract violation: unresolved instrument id "
                f"for trade conid={conid}"
            )

        resolved_trade_request: CanonicalTradeFillUpsertRequest = replace(
            trade_request,
            instrument_id=str(instrument_record.instrument_id),
        )
        resolved_trade_requests.append(resolved_trade_request)

    resolved_cashflow_requests = []
    for cashflow_request in mapped_batch.cashflow_requests:
        conid = conid_by_raw_record_id.get(cashflow_request.source_raw_record_id)
        if conid is not None and conid in instrument_id_by_conid:
            cashflow_request = replace(
                cashflow_request,
                instrument_id=str(instrument_id_by_conid[conid].instrument_id),
            )
        resolved_cashflow_requests.append(cashflow_request)

    resolved_corp_action_requests = []
    for corp_action_request in mapped_batch.corp_action_requests:
        if corp_action_request.conid in instrument_id_by_conid:
            corp_action_request = replace(
                corp_action_request,
                instrument_id=str(instrument_id_by_conid[corp_action_request.conid].instrument_id),
            )
        resolved_corp_action_requests.append(corp_action_request)

    if hasattr(canonical_persistence_repository, "db_canonical_bulk_upsert"):
        canonical_persistence_repository.db_canonical_bulk_upsert(
            trade_requests=resolved_trade_requests,
            cashflow_requests=resolved_cashflow_requests,
            fx_requests=mapped_batch.fx_requests,
            corp_action_requests=resolved_corp_action_requests,
        )
    else:
        for trade_request in resolved_trade_requests:
            canonical_persistence_repository.db_canonical_trade_fill_upsert(trade_request)

        for cashflow_request in resolved_cashflow_requests:
            canonical_persistence_repository.db_canonical_cashflow_upsert(cashflow_request)

        for fx_request in mapped_batch.fx_requests:
            canonical_persistence_repository.db_canonical_fx_upsert(fx_request)

        for corp_action_request in resolved_corp_action_requests:
            canonical_persistence_repository.db_canonical_corp_action_upsert(corp_action_request)

    return {
        "instrument_upsert_count": len(mapped_batch.instrument_upsert_requests),
        "trade_fill_count": len(mapped_batch.trade_fill_requests),
        "cashflow_count": len(mapped_batch.cashflow_requests),
        "fx_count": len(mapped_batch.fx_requests),
        "corp_action_count": len(mapped_batch.corp_action_requests),
    }


def _job_canonical_upsert_instruments(
    mapped_batch: CanonicalMappingBatch,
    canonical_persistence_repository: CanonicalPersistenceRepositoryPort,
) -> dict[str, CanonicalInstrumentRecord]:
    """Upsert mapped canonical instruments and build lookup by conid.

    Args:
        mapped_batch: Mapped canonical batch.
        canonical_persistence_repository: Canonical persistence repository.

    Returns:
        dict[str, CanonicalInstrumentRecord]: Persisted instrument records keyed by conid.

    Raises:
        RuntimeError: Raised when persistence operation fails.
    """

    records_by_conid: dict[str, CanonicalInstrumentRecord] = {}
    unique_requests: dict[str, CanonicalInstrumentUpsertRequest] = {}
    for request in mapped_batch.instrument_upsert_requests:
        unique_requests[request.conid] = request

    for conid, request in unique_requests.items():
        records_by_conid[conid] = canonical_persistence_repository.db_canonical_instrument_upsert(request)

    return records_by_conid


def _job_canonical_build_conid_index(raw_records: list[RawRecordForCanonicalMapping]) -> dict[str, str]:
    """Build source raw-record identifier to conid lookup table.

    Args:
        raw_records: Raw rows for canonical mapping.

    Returns:
        dict[str, str]: Mapping of source raw record id to conid.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    conid_by_raw_record_id: dict[str, str] = {}
    for raw_record in raw_records:
        payload_conid = raw_record.source_payload.get("conid")
        if isinstance(payload_conid, str) and payload_conid.strip():
            conid_by_raw_record_id[str(raw_record.raw_record_id)] = payload_conid.strip()

    return conid_by_raw_record_id
