"""Typed interfaces for mapping-layer transformations."""

from datetime import date
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.db.interfaces import (
    CanonicalCashflowUpsertRequest,
    CanonicalCorpActionUpsertRequest,
    CanonicalFxUpsertRequest,
    CanonicalInstrumentUpsertRequest,
    CanonicalTradeFillUpsertRequest,
)


@dataclass(frozen=True)
class CanonicalEventRecord:
    """Canonical event output contract from mapping operations.

    Attributes:
        event_type: Canonical event type label.
        source_record_id: Identifier of originating raw record.
    """

    event_type: str
    source_record_id: str


class MappingContractViolationError(ValueError):
    """Raised when a raw row cannot satisfy canonical mapping contract requirements."""


@dataclass(frozen=True)
class RawRecordForMapping:
    """Typed raw row payload consumed by canonical mapping operations.

    Attributes:
        raw_record_id: Persistent raw row identifier.
        ingestion_run_id: Ingestion run identifier.
        section_name: Flex section name.
        source_row_ref: Deterministic source row reference.
        report_date_local: Optional report date in local business timezone.
        source_payload: JSON-compatible source row payload.
    """

    raw_record_id: UUID
    ingestion_run_id: UUID
    section_name: str
    source_row_ref: str
    report_date_local: date | None
    source_payload: dict[str, object]


@dataclass(frozen=True)
class CanonicalMappingBatch:
    """Batch canonical mapping output grouped by canonical event type.

    Attributes:
        instrument_upsert_requests: Canonical instrument upsert requests.
        trade_fill_requests: Canonical trade-fill upsert requests.
        cashflow_requests: Canonical cashflow upsert requests.
        fx_requests: Canonical FX upsert requests.
        corp_action_requests: Canonical corporate-action upsert requests.
    """

    instrument_upsert_requests: tuple[CanonicalInstrumentUpsertRequest, ...]
    trade_fill_requests: tuple[CanonicalTradeFillUpsertRequest, ...]
    cashflow_requests: tuple[CanonicalCashflowUpsertRequest, ...]
    fx_requests: tuple[CanonicalFxUpsertRequest, ...]
    corp_action_requests: tuple[CanonicalCorpActionUpsertRequest, ...]


class MappingPort(Protocol):
    """Port definition for mapping raw rows to canonical event records."""

    def mapping_contract_version(self) -> str:
        """Return mapping contract version used for canonical transforms.

        Returns:
            str: Mapping contract version identifier.

        Raises:
            RuntimeError: Raised when version metadata cannot be resolved.
        """

    def mapping_map_raw_record(self, raw_record_id: str) -> CanonicalEventRecord:
        """Map a raw record into a canonical event contract.

        Args:
            raw_record_id: Persistent raw record identifier.

        Returns:
            CanonicalEventRecord: Canonical event mapping output.

        Raises:
            ValueError: Raised when required source fields are missing.
        """

    def mapping_build_canonical_batch(
        self,
        account_id: str,
        functional_currency: str,
        raw_records: list[RawRecordForMapping],
    ) -> CanonicalMappingBatch:
        """Map raw rows into canonical event UPSERT requests.

        Args:
            account_id: Internal account context identifier.
            functional_currency: Functional/base reporting currency code.
            raw_records: Raw rows to map.

        Returns:
            CanonicalMappingBatch: Grouped canonical event upsert requests.

        Raises:
            MappingContractViolationError: Raised when one row violates required mapping contract.
            ValueError: Raised when top-level input values are invalid.
        """
