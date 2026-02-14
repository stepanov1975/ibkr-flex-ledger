"""Typed interfaces for database-layer services.

All SQL and ORM access must remain in the db package and its submodules.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from app.domain import HealthStatus


class DatabaseHealthPort(Protocol):
    """Port definition for database connectivity verification."""

    def db_connection_label(self) -> str:
        """Return a stable label for the active database connection target.

        Returns:
            str: Database target label for diagnostics.

        Raises:
            RuntimeError: Raised when connection metadata is unavailable.
        """

    def db_check_health(self) -> HealthStatus:
        """Check database connectivity and return deterministic health payload.

        Returns:
            HealthStatus: Database health status payload.

        Raises:
            ConnectionError: Raised when database cannot be reached.
        """


class IngestionRunAlreadyActiveError(RuntimeError):
    """Raised when an ingestion trigger is rejected because one run is already active."""


@dataclass(frozen=True)
class IngestionRunReference:
    """Identity and input reference for one ingestion run.

    Attributes:
        period_key: Period identity key used for idempotency policy.
        flex_query_id: Upstream Flex query identifier.
        report_date_local: Optional local report date.
    """

    period_key: str
    flex_query_id: str
    report_date_local: date | None


@dataclass(frozen=True)
class IngestionRunState:
    """Runtime lifecycle and outcome state for one ingestion run.

    Attributes:
        status: Run status (`started`, `success`, `failed`).
        started_at_utc: Run start timestamp in UTC.
        ended_at_utc: Optional run end timestamp in UTC.
        duration_ms: Optional run duration in milliseconds.
        error_code: Optional deterministic error code.
        error_message: Optional human-readable error message.
        diagnostics: Optional structured diagnostics payload.
    """

    status: str
    started_at_utc: datetime
    ended_at_utc: datetime | None
    duration_ms: int | None
    error_code: str | None
    error_message: str | None
    diagnostics: list[dict[str, Any]] | None


@dataclass(frozen=True)
class IngestionRunRecord:
    """Persistence model for one ingestion run row.

    Attributes:
        ingestion_run_id: Unique run identifier.
        account_id: Internal account context identifier.
        run_type: Run type (`scheduled`, `manual`, `reprocess`).
        reference: Stable source and period reference values.
        state: Runtime lifecycle and outcome state values.
        created_at_utc: Persistence row creation timestamp in UTC.
    """

    ingestion_run_id: UUID
    account_id: str
    run_type: str
    reference: IngestionRunReference
    state: IngestionRunState
    created_at_utc: datetime


@dataclass(frozen=True)
class RawArtifactReference:
    """Immutable identity fields for one raw artifact.

    Attributes:
        account_id: Internal account context identifier.
        period_key: Ingestion period identity key.
        flex_query_id: Upstream Flex query identifier.
        payload_sha256: SHA-256 digest hex of immutable raw payload bytes.
        report_date_local: Optional local report date from payload metadata.
    """

    account_id: str
    period_key: str
    flex_query_id: str
    payload_sha256: str
    report_date_local: date | None


@dataclass(frozen=True)
class RawArtifactPersistRequest:
    """Input payload for one immutable raw artifact persistence operation.

    Attributes:
        ingestion_run_id: Ingestion run identifier.
        reference: Immutable dedupe identity values.
        source_payload: Immutable raw payload bytes from upstream.
    """

    ingestion_run_id: UUID
    reference: RawArtifactReference
    source_payload: bytes


@dataclass(frozen=True)
class RawArtifactRecord:
    """Persistence model for one immutable raw artifact row.

    Attributes:
        raw_artifact_id: Unique raw artifact identifier.
        ingestion_run_id: Ingestion run that first persisted the artifact.
        reference: Immutable dedupe identity values.
        source_payload: Immutable raw payload bytes.
        created_at_utc: Persistence row creation timestamp in UTC.
    """

    raw_artifact_id: UUID
    ingestion_run_id: UUID
    reference: RawArtifactReference
    source_payload: bytes
    created_at_utc: datetime


@dataclass(frozen=True)
class RawArtifactPersistResult:
    """Result payload for immutable raw artifact upsert.

    Attributes:
        artifact: Persisted raw artifact row.
        deduplicated: Whether existing row was reused by unique key conflict.
    """

    artifact: RawArtifactRecord
    deduplicated: bool


@dataclass(frozen=True)
class RawRecordPersistRequest:
    """Input payload for one raw row persistence operation.

    Attributes:
        ingestion_run_id: Ingestion run identifier.
        raw_artifact_id: Parent immutable raw artifact identifier.
        artifact_reference: Immutable dedupe identity values.
        report_date_local: Optional local report date from payload metadata.
        section_name: Flex section name.
        source_row_ref: Deterministic source row reference within section.
        source_payload: Source row payload as JSON-compatible object.
    """

    ingestion_run_id: UUID
    raw_artifact_id: UUID
    artifact_reference: RawArtifactReference
    report_date_local: date | None
    section_name: str
    source_row_ref: str
    source_payload: dict[str, Any]


@dataclass(frozen=True)
class RawRecordPersistResult:
    """Summary result for raw row batch persistence.

    Attributes:
        inserted_count: Number of inserted raw rows.
        deduplicated_count: Number of rows skipped by unique-key conflict.
    """

    inserted_count: int
    deduplicated_count: int


class IngestionRunRepositoryPort(Protocol):
    """Port definition for ingestion run lifecycle persistence and reads."""

    def db_ingestion_run_create_started(
        self,
        account_id: str,
        run_type: str,
        period_key: str,
        flex_query_id: str,
        report_date_local: date | None,
    ) -> IngestionRunRecord:
        """Create a new started ingestion run under a single-active-run lock.

        Args:
            account_id: Internal account identifier.
            run_type: Trigger source (`scheduled`, `manual`, `reprocess`).
            period_key: Ingestion period identity key.
            flex_query_id: Upstream Flex query id.
            report_date_local: Optional report date in local business timezone.

        Returns:
            IngestionRunRecord: Newly created run row with `started` status.

        Raises:
            IngestionRunAlreadyActiveError: Raised when another started run exists.
            ValueError: Raised when an input value is invalid.
        """

    def db_ingestion_run_finalize(
        self,
        ingestion_run_id: UUID,
        status: str,
        error_code: str | None,
        error_message: str | None,
        diagnostics: list[dict[str, Any]] | None,
    ) -> IngestionRunRecord:
        """Finalize a started ingestion run to success or failed.

        Args:
            ingestion_run_id: Run identifier to finalize.
            status: Final status (`success` or `failed`).
            error_code: Optional deterministic error code.
            error_message: Optional human-readable error message.
            diagnostics: Optional structured diagnostics payload.

        Returns:
            IngestionRunRecord: Updated run row after finalization.

        Raises:
            LookupError: Raised when the run id is not found.
            ValueError: Raised when status or payload values are invalid.
        """

    def db_ingestion_run_get_by_id(self, ingestion_run_id: UUID) -> IngestionRunRecord | None:
        """Fetch one ingestion run by primary key.

        Args:
            ingestion_run_id: Run identifier.

        Returns:
            IngestionRunRecord | None: Matching run row, or None when absent.

        Raises:
            ValueError: Raised when input id is invalid.
        """

    def db_ingestion_run_list(self, limit: int, offset: int) -> list[IngestionRunRecord]:
        """List ingestion runs ordered by latest started timestamp and id.

        Args:
            limit: Max rows to return.
            offset: Rows to skip.

        Returns:
            list[IngestionRunRecord]: Deterministically ordered run rows.

        Raises:
            ValueError: Raised when pagination arguments are invalid.
        """


class RawPersistenceRepositoryPort(Protocol):
    """Port definition for immutable raw artifact and raw row persistence."""

    def db_raw_artifact_upsert(self, request: RawArtifactPersistRequest) -> RawArtifactPersistResult:
        """Persist or reuse immutable raw artifact by dedupe identity key.

        Args:
            request: Raw artifact persistence request payload.

        Returns:
            RawArtifactPersistResult: Persisted row and dedupe indicator.

        Raises:
            ValueError: Raised when request data is invalid.
            RuntimeError: Raised when persistence fails.
        """

    def db_raw_record_insert_many(self, requests: list[RawRecordPersistRequest]) -> RawRecordPersistResult:
        """Insert raw rows with deterministic conflict handling.

        Args:
            requests: Raw row persistence requests.

        Returns:
            RawRecordPersistResult: Inserted and deduplicated row counters.

        Raises:
            ValueError: Raised when requests are invalid.
            RuntimeError: Raised when persistence fails.
        """
