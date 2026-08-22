"""Regression tests for ingestion orchestrator lifecycle transitions."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from app import bootstrap as bootstrap_module
from app.adapters import AdapterFetchResult, FlexTokenInvalidError
from app.config import AppSettings
from app.db.interfaces import (
    CanonicalCashflowUpsertRequest,
    CanonicalCorpActionUpsertRequest,
    CanonicalFxUpsertRequest,
    CanonicalInstrumentRecord,
    CanonicalInstrumentUpsertRequest,
    CanonicalTradeFillUpsertRequest,
    IngestionRunRecord,
    IngestionRunReference,
    IngestionRunState,
    RawArtifactPersistRequest,
    RawArtifactPersistResult,
    RawArtifactRecord,
    RawArtifactReference,
    RawRecordForCanonicalMapping,
    RawRecordPersistRequest,
    RawRecordPersistResult,
)
from app.jobs import IngestionJobOrchestrator, IngestionOrchestratorConfig
import app.jobs.ingestion_orchestrator as ingestion_module
from app.ledger import SnapshotBuildResult, StockLedgerSnapshotService


_ARTIFACT_OWNER_RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
_FAILED_COMPLETION_RUN_ID = UUID("00000000-0000-0000-0000-000000000002")


def test_cli_bootstrap_marks_ingestion_as_scheduled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Classify non-HTTP ingestion as scheduled for SLO reporting."""

    settings = AppSettings(
        environment_name="test-cli-ingestion",
        database_url="postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        account_id="U_TEST",
        ibkr_flex_token="token",
        ibkr_flex_query_id="query",
    )
    engine = object()
    repository = object()
    snapshot_repository = object()
    snapshot_service = object()
    adapter = object()
    orchestrator = object()
    captured_configs: list[IngestionOrchestratorConfig] = []

    monkeypatch.setattr(bootstrap_module, "config_load_settings", lambda: settings)
    monkeypatch.setattr(bootstrap_module, "db_create_engine", lambda database_url: engine)
    monkeypatch.setattr(bootstrap_module, "SQLAlchemyIngestionRunService", lambda *, engine: repository)
    monkeypatch.setattr(bootstrap_module, "SQLAlchemyRawPersistenceService", lambda *, engine: repository)
    monkeypatch.setattr(bootstrap_module, "SQLAlchemyCanonicalPersistenceService", lambda *, engine: repository)
    monkeypatch.setattr(bootstrap_module, "SQLAlchemyLedgerSnapshotService", lambda *, engine: snapshot_repository)
    monkeypatch.setattr(bootstrap_module, "StockLedgerSnapshotService", lambda *, repository: snapshot_service)
    monkeypatch.setattr(bootstrap_module, "FlexWebServiceAdapter", lambda **kwargs: adapter)

    def build_orchestrator(**kwargs: Any) -> object:
        captured_configs.append(kwargs["config"])
        return orchestrator

    monkeypatch.setattr(bootstrap_module, "IngestionJobOrchestrator", build_orchestrator)

    result = bootstrap_module.bootstrap_create_ingestion_orchestrator()

    assert result is orchestrator
    assert captured_configs[0].run_type == "scheduled"


class _RepositoryStub:
    """Repository stub that captures finalize payloads for assertions."""

    def __init__(self, run_count: int = 1, artifact_owner_status: str = "success") -> None:
        """Initialize repository stub state.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.created_runs = [self._build_run() for _ in range(run_count)]
        self.created_run = self.created_runs[0]
        self.started_runs: list[IngestionRunRecord] = []
        self.finalize_calls: list[dict[str, Any]] = []
        self.get_by_id_calls: list[UUID] = []
        self.operation_log: list[tuple[str, UUID, str | None]] = []
        artifact_owner = self._build_run(
            ingestion_run_id=_ARTIFACT_OWNER_RUN_ID,
            status=artifact_owner_status,
        )
        self.runs_by_id = {
            artifact_owner.ingestion_run_id: artifact_owner,
            **{run.ingestion_run_id: run for run in self.created_runs},
        }

    def db_ingestion_run_create_started(
        self,
        account_id: str,
        run_type: str,
        period_key: str,
        flex_query_id: str,
        report_date_local: date | None,
    ) -> IngestionRunRecord:
        """Return deterministic started run record.

        Args:
            account_id: Account identifier.
            run_type: Run type.
            period_key: Period key.
            flex_query_id: Flex query id.
            report_date_local: Optional report date.

        Returns:
            IngestionRunRecord: Started run record.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = (account_id, run_type, period_key, flex_query_id, report_date_local)
        run = self.created_runs[len(self.started_runs)]
        self.started_runs.append(run)
        self.runs_by_id[run.ingestion_run_id] = run
        return run

    def db_ingestion_run_finalize(
        self,
        ingestion_run_id: UUID,
        status: str,
        error_code: str | None,
        error_message: str | None,
        diagnostics: list[dict[str, Any]] | None,
    ) -> IngestionRunRecord:
        """Capture finalize call and return updated run record.

        Args:
            ingestion_run_id: Run id.
            status: Final run status.
            error_code: Error code when failed.
            error_message: Error message when failed.
            diagnostics: Timeline diagnostics payload.

        Returns:
            IngestionRunRecord: Updated run record.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.finalize_calls.append(
            {
                "ingestion_run_id": ingestion_run_id,
                "status": status,
                "error_code": error_code,
                "error_message": error_message,
                "diagnostics": diagnostics,
            }
        )
        self.operation_log.append(("finalize", ingestion_run_id, status))
        started_run = self.runs_by_id[ingestion_run_id]
        finalized_run = IngestionRunRecord(
            ingestion_run_id=ingestion_run_id,
            account_id=started_run.account_id,
            run_type=started_run.run_type,
            reference=started_run.reference,
            state=IngestionRunState(
                status=status,
                started_at_utc=started_run.state.started_at_utc,
                ended_at_utc=started_run.state.started_at_utc,
                duration_ms=100,
                error_code=error_code,
                error_message=error_message,
                diagnostics=diagnostics,
            ),
            created_at_utc=started_run.created_at_utc,
        )
        self.runs_by_id[ingestion_run_id] = finalized_run
        return finalized_run

    def db_ingestion_run_get_by_id(self, ingestion_run_id: UUID) -> IngestionRunRecord | None:
        """Return a configured run by id and capture owner-state reads."""

        self.get_by_id_calls.append(ingestion_run_id)
        return self.runs_by_id.get(ingestion_run_id)

    def db_ingestion_run_list(
        self,
        limit: int,
        offset: int,
        sort_by: str = "started_at_utc",
        sort_dir: str = "desc",
    ) -> list[IngestionRunRecord]:
        """Return deterministic configured runs for protocol completeness."""

        _ = (sort_by, sort_dir)
        return list(self.runs_by_id.values())[offset : offset + limit]

    def _build_run(
        self,
        ingestion_run_id: UUID | None = None,
        status: str = "started",
    ) -> IngestionRunRecord:
        """Build one deterministic run record.

        Returns:
            IngestionRunRecord: Run record in the requested state.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        started_at = datetime.now(timezone.utc)
        return IngestionRunRecord(
            ingestion_run_id=ingestion_run_id or uuid4(),
            account_id="U_TEST",
            run_type="manual",
            reference=IngestionRunReference(
                period_key="2026-02-14",
                flex_query_id="query",
                report_date_local=None,
            ),
            state=IngestionRunState(
                status=status,
                started_at_utc=started_at,
                ended_at_utc=None if status == "started" else started_at,
                duration_ms=None if status == "started" else 100,
                error_code=None,
                error_message=None,
                diagnostics=None,
            ),
            created_at_utc=started_at,
        )


class _AdapterStub:
    """Adapter stub returning deterministic payload and stage timeline."""

    def __init__(self, payload_bytes: bytes):
        """Initialize adapter stub.

        Args:
            payload_bytes: Payload to return from fetch.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self._payload_bytes = payload_bytes

    def adapter_source_name(self) -> str:
        """Return deterministic adapter source label.

        Returns:
            str: Source label.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return "stub"

    def adapter_fetch_report(self, query_id: str) -> AdapterFetchResult:
        """Return deterministic adapter fetch result.

        Args:
            query_id: Flex query id.

        Returns:
            AdapterFetchResult: Deterministic result.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = query_id
        return AdapterFetchResult(
            run_reference="REF123",
            payload_bytes=self._payload_bytes,
            stage_timeline=[{"stage": "request", "status": "completed"}],
        )


class _RawPersistenceStub:
    """Raw persistence stub returning deterministic artifact and row counters."""

    def __init__(
        self,
        artifact_deduplicated: bool = False,
        raw_insert_failures: int = 0,
        completed_ingestion_run_id: UUID | None = _ARTIFACT_OWNER_RUN_ID,
        operation_log: list[tuple[str, UUID, str | None]] | None = None,
    ) -> None:
        """Initialize deterministic raw persistence stub state.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.raw_artifact_id = uuid4()
        self.artifact_deduplicated = artifact_deduplicated
        self.raw_insert_failures = raw_insert_failures
        self.completed_ingestion_run_id = (
            completed_ingestion_run_id if artifact_deduplicated else None
        )
        self.operation_log = operation_log
        self.artifact: RawArtifactRecord | None = None
        self.raw_insert_calls = 0
        self.raw_insert_run_ids: list[UUID] = []
        self.completion_calls: list[tuple[UUID, UUID]] = []
        self.raw_rows_persisted = artifact_deduplicated

    def db_raw_artifact_upsert(self, request: RawArtifactPersistRequest) -> RawArtifactPersistResult:
        """Return deterministic artifact upsert result.

        Args:
            request: Raw artifact persist request.

        Returns:
            RawArtifactPersistResult: Deterministic raw artifact result.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        if self.artifact is None:
            self.artifact = RawArtifactRecord(
                raw_artifact_id=self.raw_artifact_id,
                ingestion_run_id=(
                    _ARTIFACT_OWNER_RUN_ID if self.artifact_deduplicated else request.ingestion_run_id
                ),
                reference=RawArtifactReference(
                    account_id=request.reference.account_id,
                    period_key=request.reference.period_key,
                    flex_query_id=request.reference.flex_query_id,
                    payload_sha256=request.reference.payload_sha256,
                    report_date_local=request.reference.report_date_local,
                ),
                source_payload=request.source_payload,
                created_at_utc=datetime.now(timezone.utc),
                completed_ingestion_run_id=self.completed_ingestion_run_id,
            )
            deduplicated = self.artifact_deduplicated
        else:
            deduplicated = True
        return RawArtifactPersistResult(
            artifact=self.artifact,
            deduplicated=deduplicated,
        )

    def db_raw_record_insert_many(
        self,
        requests: list[RawRecordPersistRequest],
    ) -> RawRecordPersistResult:
        """Return deterministic raw row insert summary.

        Args:
            requests: Raw row persistence requests.

        Returns:
            RawRecordPersistResult: Deterministic insert counters.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.raw_insert_calls += 1
        self.raw_insert_run_ids.append(requests[0].ingestion_run_id)
        if self.raw_insert_failures > 0:
            self.raw_insert_failures -= 1
            raise RuntimeError("raw row persistence failed")
        if self.raw_rows_persisted:
            return RawRecordPersistResult(inserted_count=0, deduplicated_count=len(requests))
        self.raw_rows_persisted = True
        return RawRecordPersistResult(inserted_count=len(requests), deduplicated_count=0)

    def db_raw_artifact_mark_completed(
        self,
        raw_artifact_id: UUID,
        completed_ingestion_run_id: UUID,
    ) -> None:
        """Capture completion lineage and expose it on later artifact upserts."""

        assert self.artifact is not None
        assert raw_artifact_id == self.artifact.raw_artifact_id
        self.completion_calls.append((raw_artifact_id, completed_ingestion_run_id))
        if self.operation_log is not None:
            self.operation_log.append(("mark_completed", completed_ingestion_run_id, None))
        self.artifact = replace(
            self.artifact,
            completed_ingestion_run_id=completed_ingestion_run_id,
        )


class _SnapshotServiceStub(StockLedgerSnapshotService):  # type: ignore[misc]
    """Snapshot service stub capturing automatic snapshot execution calls."""

    def __init__(self) -> None:
        """Initialize snapshot service call capture state.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.calls: list[dict[str, object]] = []
        self.build_calls = 0
        self.affected_conids: frozenset[str] | None = None
        self.affected_currencies: frozenset[str] | None = None

    def ledger_snapshot_build_and_persist(
        self,
        account_id: str,
        ingestion_run_id: str | None,
        report_date_local: str,
        functional_currency: str,
        affected_conids: frozenset[str] | None = None,
        affected_currencies: frozenset[str] | None = None,
    ) -> SnapshotBuildResult:
        """Capture snapshot trigger parameters and return deterministic result.

        Args:
            account_id: Internal account identifier.
            ingestion_run_id: Ingestion run identifier.
            report_date_local: Flex statement business date.
            functional_currency: Functional/base reporting currency code.

        Returns:
            object: Lightweight snapshot build result.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.build_calls += 1
        self.affected_conids = affected_conids
        self.affected_currencies = affected_currencies
        self.calls.append(
            {
                "account_id": account_id,
                "ingestion_run_id": ingestion_run_id,
                "report_date_local": report_date_local,
                "functional_currency": functional_currency,
                "affected_conids": affected_conids,
                "affected_currencies": affected_currencies,
            }
        )
        return SnapshotBuildResult(
            report_date_local="2026-02-20",
            snapshot_row_count=1,
            position_lot_row_count=1,
            missing_solid_valuation_count=0,
            broker_position_match_count=2,
            broker_position_mismatch_count=1,
            broker_only_position_count=1,
            broker_absent_nonzero_fifo_count=1,
        )


def test_jobs_ingestion_orchestrator_marks_failed_on_missing_required_sections() -> None:
    """Finalize ingestion run as failed on required-section preflight failure.

    Returns:
        None: Assertions validate behavior.

    Raises:
        AssertionError: Raised when status or error code do not match expectations.
    """

    incomplete_payload = (
        b"<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement><Trades />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    repository_stub = _RepositoryStub()
    adapter_stub = _AdapterStub(payload_bytes=incomplete_payload)
    raw_persistence_stub = _RawPersistenceStub()
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository_stub,
        raw_persistence_repository=raw_persistence_stub,
        flex_adapter=adapter_stub,
        config=IngestionOrchestratorConfig(account_id="U_TEST", flex_query_id="query"),
    )

    result = orchestrator.job_execute(job_name="ingestion_run")

    assert result.status == "failed"
    assert repository_stub.finalize_calls[0]["status"] == "failed"
    assert repository_stub.finalize_calls[0]["error_code"] == "MISSING_REQUIRED_SECTION"


def test_jobs_ingestion_orchestrator_marks_success_with_stage_timeline() -> None:
    """Finalize ingestion run as success and persist stage timeline payload.

    Returns:
        None: Assertions validate behavior.

    Raises:
        AssertionError: Raised when status or timeline data are unexpected.
    """

    complete_payload = (
            b"<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement reportDate=\"20260220\">"
        b"<Trades /><OpenPositions /><CashTransactions /><CorporateActions />"
        b"<ConversionRates /><SecuritiesInfo /><AccountInformation />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    repository_stub = _RepositoryStub()
    adapter_stub = _AdapterStub(payload_bytes=complete_payload)
    raw_persistence_stub = _RawPersistenceStub()
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository_stub,
        raw_persistence_repository=raw_persistence_stub,
        flex_adapter=adapter_stub,
        config=IngestionOrchestratorConfig(account_id="U_TEST", flex_query_id="query"),
    )

    result = orchestrator.job_execute(job_name="ingestion_run")

    assert result.status == "success"
    assert repository_stub.finalize_calls[0]["status"] == "success"


def test_jobs_ingestion_orchestrator_runs_snapshot_stage_on_success() -> None:
    """Trigger automatic snapshot build at the end of successful ingestion.

    Returns:
        None: Assertions validate snapshot-stage execution semantics.

    Raises:
        AssertionError: Raised when snapshot stage is not triggered.
    """

    complete_payload = (
        b"<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement reportDate=\"20260220\">"
        b"<Trades /><OpenPositions /><CashTransactions /><CorporateActions />"
        b"<ConversionRates /><SecuritiesInfo /><AccountInformation />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    repository_stub = _RepositoryStub()
    adapter_stub = _AdapterStub(payload_bytes=complete_payload)
    raw_persistence_stub = _RawPersistenceStub()
    snapshot_service_stub = _SnapshotServiceStub()
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository_stub,
        raw_persistence_repository=raw_persistence_stub,
        flex_adapter=adapter_stub,
        config=IngestionOrchestratorConfig(account_id="U_TEST", flex_query_id="query"),
        snapshot_service=snapshot_service_stub,
    )

    result = orchestrator.job_execute(job_name="ingestion_run")

    assert result.status == "success"
    assert len(snapshot_service_stub.calls) == 1
    assert snapshot_service_stub.calls[0]["report_date_local"] == "2026-02-20"
    assert snapshot_service_stub.calls[0]["functional_currency"] == "USD"
    details = _completed_stage_details(repository_stub)["snapshot"]
    assert details["broker_position_match_count"] == 2
    assert details["broker_position_mismatch_count"] == 1
    assert details["broker_only_position_count"] == 1
    assert details["broker_absent_nonzero_fifo_count"] == 1
    snapshot_timeline_events = [
        event for event in repository_stub.finalize_calls[0]["diagnostics"] if event.get("stage") == "snapshot"
    ]
    assert snapshot_timeline_events[-1]["status"] == "completed"


def test_jobs_ingestion_orchestrator_returns_failed_result_on_adapter_timeout() -> None:
    """Return failed result and finalize diagnostics when adapter times out.

    Returns:
        None: Assertions validate graceful failure behavior.

    Raises:
        AssertionError: Raised when timeout is propagated as unhandled exception.
    """

    complete_payload = (
        b"<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement>"
        b"<Trades /><OpenPositions /><CashTransactions /><CorporateActions />"
        b"<ConversionRates /><SecuritiesInfo /><AccountInformation />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )

    class _TimeoutAdapterStub(_AdapterStub):
        def adapter_fetch_report(self, query_id: str) -> AdapterFetchResult:
            _ = query_id
            raise TimeoutError("upstream timeout")

    repository_stub = _RepositoryStub()
    adapter_stub = _TimeoutAdapterStub(payload_bytes=complete_payload)
    raw_persistence_stub = _RawPersistenceStub()
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository_stub,
        raw_persistence_repository=raw_persistence_stub,
        flex_adapter=adapter_stub,
        config=IngestionOrchestratorConfig(account_id="U_TEST", flex_query_id="query"),
    )

    result = orchestrator.job_execute(job_name="ingestion_run")

    assert result.status == "failed"
    assert repository_stub.finalize_calls[0]["status"] == "failed"
    assert repository_stub.finalize_calls[0]["error_code"] == "INGESTION_TIMEOUT_ERROR"
    diagnostics = repository_stub.finalize_calls[0]["diagnostics"]
    assert isinstance(diagnostics, list)
    failed_run_events = [event for event in diagnostics if event.get("stage") == "run" and event.get("status") == "failed"]
    assert len(failed_run_events) == 1


def test_jobs_ingestion_orchestrator_maps_typed_token_error_to_deterministic_code() -> None:
    """Map typed token-lifecycle adapter errors to dedicated ingestion error code.

    Returns:
        None: Assertions validate deterministic error-code mapping.

    Raises:
        AssertionError: Raised when typed token failure is not mapped correctly.
    """

    complete_payload = (
        b"<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement>"
        b"<Trades /><OpenPositions /><CashTransactions /><CorporateActions />"
        b"<ConversionRates /><SecuritiesInfo /><AccountInformation />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )

    class _TokenInvalidAdapterStub(_AdapterStub):
        def adapter_fetch_report(self, query_id: str) -> AdapterFetchResult:
            _ = query_id
            raise FlexTokenInvalidError("invalid token", error_code="1015")

    repository_stub = _RepositoryStub()
    adapter_stub = _TokenInvalidAdapterStub(payload_bytes=complete_payload)
    raw_persistence_stub = _RawPersistenceStub()
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository_stub,
        raw_persistence_repository=raw_persistence_stub,
        flex_adapter=adapter_stub,
        config=IngestionOrchestratorConfig(account_id="U_TEST", flex_query_id="query"),
    )

    result = orchestrator.job_execute(job_name="ingestion_run")

    assert result.status == "failed"
    assert repository_stub.finalize_calls[0]["error_code"] == "INGESTION_TOKEN_INVALID_ERROR"


def test_jobs_ingestion_orchestrator_finalizes_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not leave a started run active after an ordinary programming error."""

    repository = _RepositoryStub()

    def raise_unexpected(**_kwargs: object) -> object:
        raise KeyError("unexpected preflight failure")

    monkeypatch.setattr(
        ingestion_module,
        "job_section_preflight_validate_required_sections",
        raise_unexpected,
    )

    result = _build_orchestrator(repository).job_execute("ingestion_run")

    assert result.status == "failed"
    assert repository.finalize_calls[-1]["status"] == "failed"
    assert repository.finalize_calls[-1]["error_code"] == "INGESTION_UNEXPECTED_ERROR"
    assert "unexpected preflight failure" in str(repository.finalize_calls[-1]["error_message"])


def test_jobs_ingestion_orchestrator_persist_stage_contains_raw_persistence_details() -> None:
    """Require persist-stage diagnostics to include concrete raw persistence data.

    Returns:
        None: Assertions validate persisted diagnostics contract.

    Raises:
        AssertionError: Raised when persist-stage details remain placeholder-only.
    """

    complete_payload = (
        b"<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement>"
        b"<Trades transactionID=\"T1\" /><OpenPositions /><CashTransactions />"
        b"<CorporateActions /><ConversionRates /><SecuritiesInfo /><AccountInformation />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    repository_stub = _RepositoryStub()
    adapter_stub = _AdapterStub(payload_bytes=complete_payload)
    raw_persistence_stub = _RawPersistenceStub()
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository_stub,
        raw_persistence_repository=raw_persistence_stub,
        flex_adapter=adapter_stub,
        config=IngestionOrchestratorConfig(account_id="U_TEST", flex_query_id="query"),
    )

    result = orchestrator.job_execute(job_name="ingestion_run")

    assert result.status == "success"
    diagnostics = repository_stub.finalize_calls[0]["diagnostics"]
    persist_completed = [
        event
        for event in diagnostics
        if event.get("stage") == "persist" and event.get("status") == "completed"
    ]
    assert len(persist_completed) == 1
    details = persist_completed[0].get("details")
    assert isinstance(details, dict)
    assert "payload_sha256" in details
    assert "raw_artifact_id" in details
    assert "raw_record_count" in details


def test_jobs_ingestion_orchestrator_canonical_stage_contains_duration_details() -> None:
    """Require canonical stage diagnostics to include measured duration.

    Returns:
        None: Assertions validate canonical diagnostics contract.

    Raises:
        AssertionError: Raised when canonical-stage details omit duration.
    """

    complete_payload = (
        b"<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement>"
        b"<Trades transactionID=\"T1\" ibExecID=\"EXEC-1\" conid=\"265598\" buySell=\"BUY\" quantity=\"1\" "
        b"tradePrice=\"100\" currency=\"USD\" reportDate=\"2026-02-14\" dateTime=\"2026-02-14T10:00:00+00:00\" />"
        b"<OpenPositions /><CashTransactions /><CorporateActions /><ConversionRates />"
        b"<SecuritiesInfo /><AccountInformation />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    repository_stub = _RepositoryStub()
    adapter_stub = _AdapterStub(payload_bytes=complete_payload)
    raw_persistence_stub = _RawPersistenceStub()
    canonical_repository = _CanonicalRepositoryStub()
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository_stub,
        raw_persistence_repository=raw_persistence_stub,
        flex_adapter=adapter_stub,
        config=IngestionOrchestratorConfig(account_id="U_TEST", flex_query_id="query"),
        canonical_repository=canonical_repository,
    )

    result = orchestrator.job_execute(job_name="ingestion_run")

    assert result.status == "success"
    diagnostics = repository_stub.finalize_calls[0]["diagnostics"]
    canonical_completed = [
        event
        for event in diagnostics
        if event.get("stage") == "canonical_mapping" and event.get("status") == "completed"
    ]
    assert len(canonical_completed) == 1
    details = canonical_completed[0].get("details")
    assert isinstance(details, dict)
    assert details["canonical_input_row_count"] == 1
    assert "canonical_duration_ms" in details
    assert isinstance(details["canonical_duration_ms"], int)
    assert details["canonical_duration_ms"] >= 0


def _raw_row(
    section_name: str,
    source_payload: dict[str, str],
    ingestion_run_id: UUID | None = None,
) -> RawRecordForCanonicalMapping:
    """Build one deterministic raw row for orchestrator scope tests."""

    run_id = ingestion_run_id or uuid4()
    return RawRecordForCanonicalMapping(
        raw_record_id=uuid4(),
        ingestion_run_id=run_id,
        account_id="U1",
        period_key="2026-08",
        flex_query_id="query",
        report_date_local=date(2026, 8, 21),
        section_name=section_name,
        source_row_ref=f"{section_name}:row-1",
        source_payload=source_payload,
    )


class _CanonicalRepositoryStub:
    """Canonical repository stub implementing read and upsert behaviors."""

    def __init__(
        self,
        changed_rows: list[RawRecordForCanonicalMapping] | None = None,
        changed_read_failures: int = 0,
        artifact_rows: list[RawRecordForCanonicalMapping] | None = None,
        artifact_read_failures: int = 0,
    ) -> None:
        """Initialize deterministic changed rows and call counters."""

        self.changed_rows = changed_rows if changed_rows is not None else [
            RawRecordForCanonicalMapping(
                raw_record_id=uuid4(),
                ingestion_run_id=uuid4(),
                account_id="U1",
                period_key="2026-02",
                flex_query_id="query",
                report_date_local=date(2026, 2, 14),
                section_name="Trades",
                source_row_ref="Trades:Trade:transactionID=T1",
                source_payload={
                    "ibExecID": "EXEC-1",
                    "transactionID": "T1",
                    "conid": "265598",
                    "buySell": "BUY",
                    "quantity": "1",
                    "tradePrice": "100",
                    "currency": "USD",
                    "reportDate": "2026-02-14",
                    "dateTime": "2026-02-14T10:00:00+00:00",
                },
            )
        ]
        self.all_rows = self.changed_rows
        self.artifact_rows = artifact_rows if artifact_rows is not None else self.all_rows
        self.changed_read_failures = changed_read_failures
        self.artifact_read_failures = artifact_read_failures
        self.changed_read_calls = 0
        self.changed_read_run_ids: list[UUID] = []
        self.all_read_run_ids: list[UUID] = []
        self.artifact_read_ids: list[UUID] = []
        self.bulk_upsert_calls = 0

    def db_raw_record_list_changed_for_run(
        self,
        ingestion_run_id: UUID,
    ) -> list[RawRecordForCanonicalMapping]:
        """Return configured changed rows and record the delta read."""

        self.changed_read_calls += 1
        self.changed_read_run_ids.append(ingestion_run_id)
        if self.changed_read_failures > 0:
            self.changed_read_failures -= 1
            raise RuntimeError("canonical changed-row read failed")
        return self.changed_rows

    def db_raw_record_list_for_run(
        self,
        ingestion_run_id: UUID,
    ) -> list[RawRecordForCanonicalMapping]:
        """Return one deterministic trade row for canonical mapping.

        Args:
            ingestion_run_id: Ingestion run identifier.

        Returns:
            list[object]: Deterministic raw rows.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.all_read_run_ids.append(ingestion_run_id)
        return self.all_rows

    def db_raw_record_list_for_artifact(
        self,
        raw_artifact_id: UUID,
    ) -> list[RawRecordForCanonicalMapping]:
        """Return configured artifact rows and capture artifact-scoped reads."""

        self.artifact_read_ids.append(raw_artifact_id)
        if self.artifact_read_failures > 0:
            self.artifact_read_failures -= 1
            raise RuntimeError("canonical artifact-row read failed")
        return self.artifact_rows

    def db_raw_record_list_for_period(
        self,
        account_id: str,
        period_key: str,
        flex_query_id: str,
    ) -> list[RawRecordForCanonicalMapping]:
        """Return configured rows for protocol completeness."""

        _ = (account_id, period_key, flex_query_id)
        return self.all_rows

    def db_canonical_instrument_upsert_many(
        self, requests: list[CanonicalInstrumentUpsertRequest]
    ) -> list[CanonicalInstrumentRecord]:
        """Return deterministic instrument records.

        Args:
            requests: Canonical instrument upsert requests.

        Returns:
            list[CanonicalInstrumentRecord]: Canonical instrument identities.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return [
            CanonicalInstrumentRecord(
                instrument_id=uuid4(),
                account_id=request.account_id,
                conid=request.conid,
            )
            for request in requests
        ]

    def db_canonical_instrument_upsert(
        self,
        request: CanonicalInstrumentUpsertRequest,
    ) -> CanonicalInstrumentRecord:
        """Return one deterministic instrument record."""

        return self.db_canonical_instrument_upsert_many([request])[0]

    def db_canonical_trade_fill_upsert(self, request: CanonicalTradeFillUpsertRequest) -> None:
        """Accept one trade request for protocol completeness."""

        _ = request

    def db_canonical_cashflow_upsert(self, request: CanonicalCashflowUpsertRequest) -> None:
        """Accept one cashflow request for protocol completeness."""

        _ = request

    def db_canonical_fx_upsert(self, request: CanonicalFxUpsertRequest) -> None:
        """Accept one FX request for protocol completeness."""

        _ = request

    def db_canonical_corp_action_upsert(self, request: CanonicalCorpActionUpsertRequest) -> None:
        """Accept one corporate-action request for protocol completeness."""

        _ = request

    def db_canonical_bulk_upsert(
        self,
        trade_requests: list[CanonicalTradeFillUpsertRequest],
        cashflow_requests: list[CanonicalCashflowUpsertRequest],
        fx_requests: list[CanonicalFxUpsertRequest],
        corp_action_requests: list[CanonicalCorpActionUpsertRequest],
    ) -> None:
        """Accept bulk canonical requests without side effects.

        Args:
            trade_requests: Canonical trade requests.
            cashflow_requests: Canonical cashflow requests.
            fx_requests: Canonical fx requests.
            corp_action_requests: Canonical corporate-action requests.

        Returns:
            None: This stub records no state.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = (trade_requests, cashflow_requests, fx_requests, corp_action_requests)
        self.bulk_upsert_calls += 1


class _CanonicalRepositoryEmptyRunStub(_CanonicalRepositoryStub):
    """Canonical repository stub returning no run-scoped rows."""

    def __init__(self) -> None:
        """Initialize with an empty changed-row result."""

        super().__init__(changed_rows=[])

    def db_raw_record_list_changed_for_run(
        self,
        ingestion_run_id: UUID,
    ) -> list[RawRecordForCanonicalMapping]:
        """Return no changed rows for normal ingestion."""

        self.changed_read_calls += 1
        self.changed_read_run_ids.append(ingestion_run_id)
        return []

    def db_raw_record_list_for_run(
        self,
        ingestion_run_id: UUID,
    ) -> list[RawRecordForCanonicalMapping]:
        """Return no rows for run-scoped canonical mapping.

        Args:
            ingestion_run_id: Ingestion run identifier.

        Returns:
            list[object]: Empty row list.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.all_read_run_ids.append(ingestion_run_id)
        return []


def _build_orchestrator(
    ingestion_repository: _RepositoryStub,
    raw: _RawPersistenceStub | None = None,
    canonical: _CanonicalRepositoryStub | None = None,
    snapshot: _SnapshotServiceStub | None = None,
) -> IngestionJobOrchestrator:
    """Build an orchestrator with all semantic services configured."""

    payload = (
        b'<FlexQueryResponse><FlexStatements count="1"><FlexStatement reportDate="20260821">'
        b'<Trades><Trade ibExecID="DUP" /></Trades>'
        b"<OpenPositions /><CashTransactions /><CorporateActions />"
        b"<ConversionRates /><SecuritiesInfo /><AccountInformation />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    return IngestionJobOrchestrator(
        ingestion_repository=ingestion_repository,
        raw_persistence_repository=raw or _RawPersistenceStub(),
        flex_adapter=_AdapterStub(payload),
        config=IngestionOrchestratorConfig(account_id="U1", flex_query_id="query"),
        canonical_repository=canonical or _CanonicalRepositoryStub(),
        snapshot_service=snapshot or _SnapshotServiceStub(),
    )


def _completed_stage_details(repository: _RepositoryStub) -> dict[str, dict[str, object]]:
    """Index completed stage detail payloads by stage name."""

    diagnostics = repository.finalize_calls[-1]["diagnostics"]
    assert isinstance(diagnostics, list)
    return {
        str(event["stage"]): event["details"]
        for event in diagnostics
        if event.get("status") == "completed" and isinstance(event.get("details"), dict)
    }


def test_exact_duplicate_skips_raw_canonical_and_snapshot_work() -> None:
    """Stop semantic work after a successful completion marker is returned."""

    repository = _RepositoryStub()
    raw = _RawPersistenceStub(artifact_deduplicated=True)
    canonical = _CanonicalRepositoryStub()
    snapshot = _SnapshotServiceStub()
    orchestrator = _build_orchestrator(repository, raw=raw, canonical=canonical, snapshot=snapshot)

    result = orchestrator.job_execute(job_name="ingestion_run")

    assert result.status == "success"
    assert repository.get_by_id_calls == [_ARTIFACT_OWNER_RUN_ID]
    assert raw.raw_insert_calls == 0
    assert canonical.changed_read_calls == 0
    assert canonical.bulk_upsert_calls == 0
    assert snapshot.build_calls == 0
    details = _completed_stage_details(repository)
    assert details["persist"]["raw_persistence_skip_reason"] == "exact_duplicate_artifact"
    assert details["persist"]["raw_record_count"] == 0
    assert details["persist"]["raw_record_deduplicated_count"] == 7
    assert details["canonical_mapping"]["canonical_skip_reason"] == "exact_duplicate_artifact"
    assert details["snapshot"]["snapshot_skip_reason"] == "exact_duplicate_artifact"


def test_three_attempt_recovery_records_completion_then_enables_duplicate_fast_path() -> None:
    """Recover artifact rows across two failures and skip only after durable success."""

    repository = _RepositoryStub(run_count=4)
    raw = _RawPersistenceStub(
        raw_insert_failures=1,
        operation_log=repository.operation_log,
    )
    raw_row_run_id = repository.created_runs[1].ingestion_run_id
    canonical = _CanonicalRepositoryStub(
        artifact_rows=[
            _raw_row("Trades", {"conid": "100"}, ingestion_run_id=raw_row_run_id),
        ],
        changed_read_failures=1,
        artifact_read_failures=1,
    )
    snapshot = _SnapshotServiceStub()
    orchestrator = _build_orchestrator(repository, raw=raw, canonical=canonical, snapshot=snapshot)

    results = [orchestrator.job_execute("ingestion_run") for _ in range(4)]

    run_ids = [run.ingestion_run_id for run in repository.started_runs]
    assert [result.status for result in results] == ["failed", "failed", "success", "success"]
    assert raw.raw_insert_run_ids == run_ids[:3]
    assert canonical.changed_read_run_ids == []
    assert canonical.all_read_run_ids == []
    assert canonical.artifact_read_ids == [raw.raw_artifact_id, raw.raw_artifact_id]
    assert canonical.bulk_upsert_calls == 1
    assert snapshot.build_calls == 1
    assert snapshot.calls[0]["ingestion_run_id"] == str(raw_row_run_id)
    assert raw.completion_calls == [(raw.raw_artifact_id, run_ids[2])]
    assert repository.get_by_id_calls == [run_ids[2]]
    marker_index = repository.operation_log.index(("mark_completed", run_ids[2], None))
    success_index = repository.operation_log.index(("finalize", run_ids[2], "success"))
    assert marker_index < success_index
    details = _completed_stage_details(repository)
    assert details["persist"]["raw_persistence_skip_reason"] == "exact_duplicate_artifact"
    assert details["canonical_mapping"]["canonical_skip_reason"] == "exact_duplicate_artifact"
    assert details["snapshot"]["snapshot_skip_reason"] == "exact_duplicate_artifact"


def test_artifact_rows_with_multiple_ingestion_runs_fail_recovery_explicitly() -> None:
    """Reject ambiguous OpenPositions lineage instead of guessing a semantic run."""

    repository = _RepositoryStub(artifact_owner_status="failed")
    raw = _RawPersistenceStub(
        artifact_deduplicated=True,
        completed_ingestion_run_id=None,
    )
    canonical = _CanonicalRepositoryStub(
        artifact_rows=[
            _raw_row("Trades", {"conid": "100"}, ingestion_run_id=uuid4()),
            _raw_row("OpenPositions", {"conid": "100"}, ingestion_run_id=uuid4()),
        ]
    )
    snapshot = _SnapshotServiceStub()

    result = _build_orchestrator(
        repository,
        raw=raw,
        canonical=canonical,
        snapshot=snapshot,
    ).job_execute("ingestion_run")

    assert result.status == "failed"
    assert repository.finalize_calls[-1]["error_code"] == "INGESTION_UNEXPECTED_ERROR"
    assert "multiple ingestion runs" in str(repository.finalize_calls[-1]["error_message"])
    assert canonical.bulk_upsert_calls == 0
    assert snapshot.build_calls == 0
    assert raw.completion_calls == []


def test_completion_marker_target_must_be_successful_before_duplicate_fast_path() -> None:
    """Replay an artifact when its completion marker references a failed run."""

    repository = _RepositoryStub()
    failed_completion = repository._build_run(
        ingestion_run_id=_FAILED_COMPLETION_RUN_ID,
        status="failed",
    )
    repository.runs_by_id[_FAILED_COMPLETION_RUN_ID] = failed_completion
    raw = _RawPersistenceStub(
        artifact_deduplicated=True,
        completed_ingestion_run_id=_FAILED_COMPLETION_RUN_ID,
    )
    canonical = _CanonicalRepositoryStub(
        artifact_rows=[_raw_row("Trades", {"conid": "100"})]
    )
    snapshot = _SnapshotServiceStub()

    result = _build_orchestrator(
        repository,
        raw=raw,
        canonical=canonical,
        snapshot=snapshot,
    ).job_execute("ingestion_run")

    assert result.status == "success"
    assert repository.get_by_id_calls == [_FAILED_COMPLETION_RUN_ID]
    assert raw.raw_insert_calls == 1
    assert canonical.artifact_read_ids == [raw.raw_artifact_id]
    assert canonical.bulk_upsert_calls == 1
    assert snapshot.build_calls == 1


def test_distinct_artifact_reads_changed_rows_and_passes_incremental_scope() -> None:
    """Map only changed rows and pass their immutable scope to snapshots."""

    repository = _RepositoryStub()
    canonical = _CanonicalRepositoryStub(
        changed_rows=[
            _raw_row("Trades", {"conid": "100"}),
            _raw_row("ConversionRates", {"fromCurrency": "EUR"}),
        ]
    )
    snapshot = _SnapshotServiceStub()

    _build_orchestrator(repository, canonical=canonical, snapshot=snapshot).job_execute("ingestion_run")

    assert canonical.changed_read_calls == 1
    assert snapshot.affected_conids == frozenset({"100"})
    assert snapshot.affected_currencies == frozenset({"EUR"})
    assert _completed_stage_details(repository)["snapshot"]["snapshot_scope_mode"] == "incremental"


def test_unscopable_changed_row_falls_back_to_full_snapshot() -> None:
    """Use an explicit full rebuild when a changed semantic row is unsafe to scope."""

    repository = _RepositoryStub()
    canonical = _CanonicalRepositoryStub(changed_rows=[_raw_row("Trades", {"symbol": "AAA"})])
    snapshot = _SnapshotServiceStub()

    _build_orchestrator(repository, canonical=canonical, snapshot=snapshot).job_execute("ingestion_run")

    assert snapshot.affected_conids is None
    assert snapshot.affected_currencies is None
    details = _completed_stage_details(repository)["snapshot"]
    assert details["snapshot_scope_mode"] == "full_fallback"
    assert details["snapshot_full_rebuild_reason"] == "unscopable_changed_row:Trades:missing_conid"


def test_ingestion_completed_stages_use_distinct_monotonic_operation_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measure every operation from its own deterministic monotonic boundary."""

    repository = _RepositoryStub()
    clock_values = iter(
        [
            0,
            2_000_000,
            10_000_000,
            13_000_000,
            20_000_000,
            25_000_000,
            30_000_000,
            37_000_000,
            40_000_000,
            51_000_000,
            60_000_000,
            73_000_000,
            80_000_000,
            97_000_000,
        ]
    )
    monkeypatch.setattr(ingestion_module, "perf_counter_ns", lambda: next(clock_values))

    _build_orchestrator(repository).job_execute("ingestion_run")

    details = _completed_stage_details(repository)
    expected = {
        ("preflight", "preflight_duration_ms"): 2,
        ("xml_extraction", "xml_extraction_duration_ms"): 3,
        ("persist", "artifact_persistence_duration_ms"): 5,
        ("persist", "raw_persistence_duration_ms"): 7,
        ("canonical_mapping", "canonical_raw_read_duration_ms"): 11,
        ("canonical_mapping", "canonical_duration_ms"): 13,
        ("snapshot", "snapshot_duration_ms"): 17,
    }
    assert {
        (stage, key): details[stage][key]
        for stage, key in expected
    } == expected


def test_distinct_artifact_preserves_full_snapshot_when_canonical_repository_is_absent() -> None:
    """Keep the established full snapshot call when canonical wiring is omitted."""

    repository = _RepositoryStub()
    snapshot = _SnapshotServiceStub()
    raw = _RawPersistenceStub()
    payload = (
        b'<FlexQueryResponse><FlexStatements count="1"><FlexStatement reportDate="20260821">'
        b"<Trades /><OpenPositions /><CashTransactions /><CorporateActions />"
        b"<ConversionRates /><SecuritiesInfo /><AccountInformation />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository,
        raw_persistence_repository=raw,
        flex_adapter=_AdapterStub(payload),
        config=IngestionOrchestratorConfig(account_id="U1", flex_query_id="query"),
        snapshot_service=snapshot,
    )

    result = orchestrator.job_execute("ingestion_run")

    assert result.status == "success"
    assert snapshot.build_calls == 1
    assert snapshot.affected_conids is None
    assert snapshot.affected_currencies is None
    assert raw.completion_calls == []


def test_exact_duplicate_skips_snapshot_when_canonical_repository_is_absent() -> None:
    """Skip configured snapshot work without requiring canonical wiring."""

    repository = _RepositoryStub()
    raw = _RawPersistenceStub(artifact_deduplicated=True)
    snapshot = _SnapshotServiceStub()
    payload = (
        b'<FlexQueryResponse><FlexStatements count="1"><FlexStatement reportDate="20260821">'
        b"<Trades /><OpenPositions /><CashTransactions /><CorporateActions />"
        b"<ConversionRates /><SecuritiesInfo /><AccountInformation />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository,
        raw_persistence_repository=raw,
        flex_adapter=_AdapterStub(payload),
        config=IngestionOrchestratorConfig(account_id="U1", flex_query_id="query"),
        snapshot_service=snapshot,
    )

    result = orchestrator.job_execute("ingestion_run")

    assert result.status == "success"
    assert raw.raw_insert_calls == 0
    assert snapshot.build_calls == 0
    assert _completed_stage_details(repository)["snapshot"]["snapshot_skip_reason"] == "exact_duplicate_artifact"


def test_empty_changed_row_scope_skips_configured_snapshot_service() -> None:
    """Complete with a no-op when no changed row affects snapshot state."""

    repository = _RepositoryStub()
    canonical = _CanonicalRepositoryStub(changed_rows=[])
    snapshot = _SnapshotServiceStub()

    result = _build_orchestrator(repository, canonical=canonical, snapshot=snapshot).job_execute("ingestion_run")

    assert result.status == "success"
    assert snapshot.build_calls == 0
    assert _completed_stage_details(repository)["snapshot"]["snapshot_scope_mode"] == "skipped"


def test_absent_snapshot_service_retains_skip_diagnostic() -> None:
    """Keep the established skip reason for a duplicate when snapshot wiring is omitted."""

    repository = _RepositoryStub()
    payload = (
        b'<FlexQueryResponse><FlexStatements count="1"><FlexStatement reportDate="20260821">'
        b"<Trades /><OpenPositions /><CashTransactions /><CorporateActions />"
        b"<ConversionRates /><SecuritiesInfo /><AccountInformation />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository,
        raw_persistence_repository=_RawPersistenceStub(artifact_deduplicated=True),
        flex_adapter=_AdapterStub(payload),
        config=IngestionOrchestratorConfig(account_id="U1", flex_query_id="query"),
        canonical_repository=_CanonicalRepositoryStub(),
    )

    result = orchestrator.job_execute("ingestion_run")

    assert result.status == "success"
    details = _completed_stage_details(repository)["snapshot"]
    assert details["snapshot_skip_reason"] == "snapshot_service_not_configured"


def test_jobs_ingestion_orchestrator_canonical_stage_skips_when_run_has_no_new_raw_rows() -> None:
    """Mark canonical stage as skipped when run-scoped row set is empty.

    Returns:
        None: Assertions validate canonical skip diagnostics.

    Raises:
        AssertionError: Raised when skip diagnostics are missing.
    """

    complete_payload = (
        b"<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement>"
        b"<Trades transactionID=\"T1\" /><OpenPositions /><CashTransactions />"
        b"<CorporateActions /><ConversionRates /><SecuritiesInfo /><AccountInformation />"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )
    repository_stub = _RepositoryStub()
    adapter_stub = _AdapterStub(payload_bytes=complete_payload)
    raw_persistence_stub = _RawPersistenceStub()
    canonical_repository = _CanonicalRepositoryEmptyRunStub()
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository_stub,
        raw_persistence_repository=raw_persistence_stub,
        flex_adapter=adapter_stub,
        config=IngestionOrchestratorConfig(account_id="U_TEST", flex_query_id="query"),
        canonical_repository=canonical_repository,
    )

    result = orchestrator.job_execute(job_name="ingestion_run")

    assert result.status == "success"
    diagnostics = repository_stub.finalize_calls[0]["diagnostics"]
    canonical_completed = [
        event
        for event in diagnostics
        if event.get("stage") == "canonical_mapping" and event.get("status") == "completed"
    ]
    assert len(canonical_completed) == 1
    details = canonical_completed[0].get("details")
    assert isinstance(details, dict)
    assert details["canonical_input_row_count"] == 0
    assert details["canonical_skip_reason"] == "no_new_raw_rows_for_run"
    assert raw_persistence_stub.completion_calls == []
