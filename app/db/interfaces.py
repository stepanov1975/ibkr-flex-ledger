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


@dataclass(frozen=True)
class RawRecordForCanonicalMapping:
    """Typed raw row payload required by canonical mapping workflows.

    Attributes:
        raw_record_id: Persistent raw row identifier.
        ingestion_run_id: Ingestion run that produced the row.
        account_id: Internal account context identifier.
        period_key: Ingestion period identity key.
        flex_query_id: Upstream Flex query identifier.
        report_date_local: Optional local report date.
        section_name: Flex section name.
        source_row_ref: Deterministic source row reference.
        source_payload: JSON-compatible source payload object.
    """

    raw_record_id: UUID
    ingestion_run_id: UUID
    account_id: str
    period_key: str
    flex_query_id: str
    report_date_local: date | None
    section_name: str
    source_row_ref: str
    source_payload: dict[str, Any]


@dataclass(frozen=True)
class CanonicalTradeFillUpsertRequest:
    """Canonical trade-fill upsert input contract.

    Attributes:
        account_id: Internal account context identifier.
        instrument_id: Canonical instrument identifier.
        ingestion_run_id: Ingestion run identifier.
        source_raw_record_id: Source raw row identifier.
        ib_exec_id: IB execution identity.
        transaction_id: Optional broker transaction identifier.
        trade_timestamp_utc: Trade timestamp in UTC ISO-8601 format.
        report_date_local: Local business date in YYYY-MM-DD format.
        side: Trade side (`BUY` or `SELL`).
        quantity: Trade quantity decimal string.
        price: Trade price decimal string.
        cost: Optional cost decimal string.
        commission: Optional commission decimal string.
        fees: Optional fees decimal string.
        realized_pnl: Optional realized PnL decimal string.
        net_cash: Optional net cash decimal string.
        net_cash_in_base: Optional net cash in base currency decimal string.
        fx_rate_to_base: Optional FX rate decimal string.
        currency: Trade currency code.
        functional_currency: Functional/base currency code.
    """

    account_id: str
    instrument_id: str
    ingestion_run_id: str
    source_raw_record_id: str
    ib_exec_id: str
    transaction_id: str | None
    trade_timestamp_utc: str
    report_date_local: str
    side: str
    quantity: str
    price: str
    cost: str | None
    commission: str | None
    fees: str | None
    realized_pnl: str | None
    net_cash: str | None
    net_cash_in_base: str | None
    fx_rate_to_base: str | None
    currency: str
    functional_currency: str


@dataclass(frozen=True)
class CanonicalCashflowUpsertRequest:
    """Canonical cashflow upsert input contract.

    Attributes:
        account_id: Internal account context identifier.
        instrument_id: Optional canonical instrument identifier.
        ingestion_run_id: Ingestion run identifier.
        source_raw_record_id: Source raw row identifier.
        transaction_id: Broker transaction identifier.
        cash_action: Canonical cash action label.
        report_date_local: Local business date in YYYY-MM-DD format.
        effective_at_utc: Optional effective timestamp in UTC ISO-8601 format.
        amount: Cash amount decimal string.
        amount_in_base: Optional base-currency cash amount decimal string.
        currency: Cash currency code.
        functional_currency: Functional/base currency code.
        withholding_tax: Optional withholding tax decimal string.
        fees: Optional fees decimal string.
    """

    account_id: str
    instrument_id: str | None
    ingestion_run_id: str
    source_raw_record_id: str
    transaction_id: str
    cash_action: str
    report_date_local: str
    effective_at_utc: str | None
    amount: str
    amount_in_base: str | None
    currency: str
    functional_currency: str
    withholding_tax: str | None
    fees: str | None


@dataclass(frozen=True)
class CanonicalFxUpsertRequest:
    """Canonical FX event upsert input contract.

    Attributes:
        account_id: Internal account context identifier.
        ingestion_run_id: Ingestion run identifier.
        source_raw_record_id: Source raw row identifier.
        transaction_id: Broker transaction identifier.
        report_date_local: Local business date in YYYY-MM-DD format.
        currency: Source currency code.
        functional_currency: Functional/base currency code.
        fx_rate: Optional FX rate decimal string.
        fx_source: Deterministic FX source label.
        provisional: Whether FX value is provisional.
        diagnostic_code: Optional deterministic diagnostic code.
    """

    account_id: str
    ingestion_run_id: str
    source_raw_record_id: str
    transaction_id: str
    report_date_local: str
    currency: str
    functional_currency: str
    fx_rate: str | None
    fx_source: str
    provisional: bool
    diagnostic_code: str | None


@dataclass(frozen=True)
class CanonicalCorpActionUpsertRequest:
    """Canonical corporate-action upsert input contract.

    Attributes:
        account_id: Internal account context identifier.
        instrument_id: Optional canonical instrument identifier.
        conid: Canonical IB instrument identifier.
        ingestion_run_id: Ingestion run identifier.
        source_raw_record_id: Source raw row identifier.
        action_id: Optional broker action identifier.
        transaction_id: Optional broker transaction identifier.
        reorg_code: Canonical corporate-action code.
        report_date_local: Local business date in YYYY-MM-DD format.
        description: Optional free-text description.
        requires_manual: Whether manual workflow is required.
        provisional: Whether downstream values are provisional.
        manual_case_id: Optional manual case identifier string.
    """

    account_id: str
    instrument_id: str | None
    conid: str
    ingestion_run_id: str
    source_raw_record_id: str
    action_id: str | None
    transaction_id: str | None
    reorg_code: str
    report_date_local: str
    description: str | None
    requires_manual: bool
    provisional: bool
    manual_case_id: str | None


@dataclass(frozen=True)
class CanonicalInstrumentUpsertRequest:
    """Canonical instrument upsert input contract with conid-first identity.

    Attributes:
        account_id: Internal account context identifier.
        conid: Canonical IB instrument identifier.
        symbol: Broker symbol.
        local_symbol: Optional broker local symbol.
        isin: Optional ISIN alias.
        cusip: Optional CUSIP alias.
        figi: Optional FIGI alias.
        asset_category: IB asset category.
        currency: Instrument currency code.
        description: Optional instrument description.
    """

    account_id: str
    conid: str
    symbol: str
    local_symbol: str | None
    isin: str | None
    cusip: str | None
    figi: str | None
    asset_category: str
    currency: str
    description: str | None


@dataclass(frozen=True)
class CanonicalInstrumentRecord:
    """Canonical instrument persistence record.

    Attributes:
        instrument_id: Canonical instrument identifier.
        account_id: Internal account context identifier.
        conid: Canonical IB instrument identifier.
    """

    instrument_id: UUID
    account_id: str
    conid: str


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

    def db_ingestion_run_list(
        self,
        limit: int,
        offset: int,
        sort_by: str = "started_at_utc",
        sort_dir: str = "desc",
    ) -> list[IngestionRunRecord]:
        """List ingestion runs with deterministic endpoint sort support.

        Args:
            limit: Max rows to return.
            offset: Rows to skip.
            sort_by: Sort field name.
            sort_dir: Sort direction (`asc` or `desc`).

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


class RawRecordReadRepositoryPort(Protocol):
    """Port definition for raw-row reads used by canonical mapping workflows."""

    def db_raw_record_list_for_run(self, ingestion_run_id: UUID) -> list[RawRecordForCanonicalMapping]:
        """List raw rows for one ingestion run identity.

        Args:
            ingestion_run_id: Ingestion run identifier.

        Returns:
            list[RawRecordForCanonicalMapping]: Deterministically ordered raw rows.

        Raises:
            ValueError: Raised when input values are invalid.
            RuntimeError: Raised when read operation fails.
        """

    def db_raw_record_list_for_period(
        self,
        account_id: str,
        period_key: str,
        flex_query_id: str,
    ) -> list[RawRecordForCanonicalMapping]:
        """List raw rows for one account/period/query identity.

        Args:
            account_id: Internal account identifier.
            period_key: Ingestion period identity key.
            flex_query_id: Upstream Flex query id.

        Returns:
            list[RawRecordForCanonicalMapping]: Deterministically ordered raw rows.

        Raises:
            ValueError: Raised when input values are invalid.
            RuntimeError: Raised when read operation fails.
        """


class CanonicalPersistenceRepositoryPort(Protocol):
    """Port definition for canonical event and instrument UPSERT operations."""

    def db_canonical_instrument_upsert(self, request: CanonicalInstrumentUpsertRequest) -> CanonicalInstrumentRecord:
        """Persist or reuse canonical instrument by conid-first identity.

        Args:
            request: Instrument upsert request.

        Returns:
            CanonicalInstrumentRecord: Persisted canonical instrument record.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence operation fails.
        """

    def db_canonical_trade_fill_upsert(self, request: CanonicalTradeFillUpsertRequest) -> None:
        """UPSERT one canonical trade-fill event by frozen natural key.

        Args:
            request: Canonical trade-fill upsert request.

        Returns:
            None: Event upsert is persisted as a side effect.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence operation fails.
        """

    def db_canonical_cashflow_upsert(self, request: CanonicalCashflowUpsertRequest) -> None:
        """UPSERT one canonical cashflow event by frozen natural key.

        Args:
            request: Canonical cashflow upsert request.

        Returns:
            None: Event upsert is persisted as a side effect.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence operation fails.
        """

    def db_canonical_fx_upsert(self, request: CanonicalFxUpsertRequest) -> None:
        """UPSERT one canonical FX event by frozen natural key.

        Args:
            request: Canonical FX upsert request.

        Returns:
            None: Event upsert is persisted as a side effect.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence operation fails.
        """

    def db_canonical_corp_action_upsert(self, request: CanonicalCorpActionUpsertRequest) -> None:
        """UPSERT one canonical corporate-action event by frozen natural key.

        Args:
            request: Canonical corporate-action upsert request.

        Returns:
            None: Event upsert is persisted as a side effect.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence operation fails.
        """
