"""Regression tests for ingestion orchestrator lifecycle transitions."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.adapters import AdapterFetchResult, FlexTokenInvalidError
from app.db.interfaces import (
    IngestionRunRecord,
    IngestionRunReference,
    IngestionRunState,
    RawArtifactPersistResult,
    RawArtifactRecord,
    RawArtifactReference,
    RawRecordPersistResult,
)
from app.jobs import IngestionJobOrchestrator, IngestionOrchestratorConfig


class _RepositoryStub:
    """Repository stub that captures finalize payloads for assertions."""

    def __init__(self):
        """Initialize repository stub state.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.created_run = self._build_started_record()
        self.finalize_calls: list[dict[str, object]] = []

    def db_ingestion_run_create_started(
        self,
        account_id: str,
        run_type: str,
        period_key: str,
        flex_query_id: str,
        report_date_local,
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
        return self.created_run

    def db_ingestion_run_finalize(
        self,
        ingestion_run_id: UUID,
        status: str,
        error_code: str | None,
        error_message: str | None,
        diagnostics,
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
        return IngestionRunRecord(
            ingestion_run_id=ingestion_run_id,
            account_id=self.created_run.account_id,
            run_type=self.created_run.run_type,
            reference=self.created_run.reference,
            state=IngestionRunState(
                status=status,
                started_at_utc=self.created_run.state.started_at_utc,
                ended_at_utc=self.created_run.state.started_at_utc,
                duration_ms=100,
                error_code=error_code,
                error_message=error_message,
                diagnostics=diagnostics,
            ),
            created_at_utc=self.created_run.created_at_utc,
        )

    def _build_started_record(self) -> IngestionRunRecord:
        """Build started run record.

        Returns:
            IngestionRunRecord: Started run record.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        started_at = datetime.now(timezone.utc)
        return IngestionRunRecord(
            ingestion_run_id=uuid4(),
            account_id="U_TEST",
            run_type="manual",
            reference=IngestionRunReference(
                period_key="2026-02-14",
                flex_query_id="query",
                report_date_local=None,
            ),
            state=IngestionRunState(
                status="started",
                started_at_utc=started_at,
                ended_at_utc=None,
                duration_ms=None,
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

    def __init__(self):
        """Initialize deterministic raw persistence stub state.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.raw_artifact_id = uuid4()

    def db_raw_artifact_upsert(self, request) -> RawArtifactPersistResult:
        """Return deterministic artifact upsert result.

        Args:
            request: Raw artifact persist request.

        Returns:
            RawArtifactPersistResult: Deterministic raw artifact result.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return RawArtifactPersistResult(
            artifact=RawArtifactRecord(
                raw_artifact_id=self.raw_artifact_id,
                ingestion_run_id=request.ingestion_run_id,
                reference=RawArtifactReference(
                    account_id=request.reference.account_id,
                    period_key=request.reference.period_key,
                    flex_query_id=request.reference.flex_query_id,
                    payload_sha256=request.reference.payload_sha256,
                    report_date_local=request.reference.report_date_local,
                ),
                source_payload=request.source_payload,
                created_at_utc=datetime.now(timezone.utc),
            ),
            deduplicated=False,
        )

    def db_raw_record_insert_many(self, requests) -> RawRecordPersistResult:
        """Return deterministic raw row insert summary.

        Args:
            requests: Raw row persistence requests.

        Returns:
            RawRecordPersistResult: Deterministic insert counters.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return RawRecordPersistResult(inserted_count=len(requests), deduplicated_count=0)


class _SnapshotServiceStub:
    """Snapshot service stub capturing automatic snapshot execution calls."""

    def __init__(self):
        """Initialize snapshot service call capture state.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.calls: list[dict[str, str | None]] = []

    def ledger_snapshot_build_and_persist(
        self,
        account_id: str,
        ingestion_run_id: str | None,
        run_completed_at_utc: str,
    ):
        """Capture snapshot trigger parameters and return deterministic result.

        Args:
            account_id: Internal account identifier.
            ingestion_run_id: Ingestion run identifier.
            run_completed_at_utc: Run completion UTC timestamp.

        Returns:
            object: Lightweight snapshot build result.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.calls.append(
            {
                "account_id": account_id,
                "ingestion_run_id": ingestion_run_id,
                "run_completed_at_utc": run_completed_at_utc,
            }
        )
        return type(
            "SnapshotResult",
            (),
            {
                "report_date_local": "2026-02-20",
                "snapshot_row_count": 1,
                "position_lot_row_count": 1,
            },
        )()


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
        b"<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement>"
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
        b"<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement>"
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


class _CanonicalRepositoryStub:
    """Canonical repository stub implementing read and upsert behaviors."""

    def db_raw_record_list_for_run(self, ingestion_run_id):
        """Return one deterministic trade row for canonical mapping.

        Args:
            ingestion_run_id: Ingestion run identifier.

        Returns:
            list[object]: Deterministic raw rows.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = ingestion_run_id
        return [
            type(
                "RawRow",
                (),
                {
                    "raw_record_id": uuid4(),
                    "ingestion_run_id": uuid4(),
                    "section_name": "Trades",
                    "source_row_ref": "Trades:Trade:transactionID=T1",
                    "report_date_local": None,
                    "source_payload": {
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
                },
            )()
        ]

    def db_canonical_instrument_upsert(self, request):
        """Return deterministic instrument record.

        Args:
            request: Canonical instrument upsert request.

        Returns:
            object: Minimal instrument identity.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return type("InstrumentRecord", (), {"instrument_id": uuid4(), "account_id": request.account_id, "conid": request.conid})()

    def db_canonical_bulk_upsert(self, trade_requests, cashflow_requests, fx_requests, corp_action_requests) -> None:
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


class _CanonicalRepositoryEmptyRunStub(_CanonicalRepositoryStub):
    """Canonical repository stub returning no run-scoped rows."""

    def db_raw_record_list_for_run(self, ingestion_run_id):
        """Return no rows for run-scoped canonical mapping.

        Args:
            ingestion_run_id: Ingestion run identifier.

        Returns:
            list[object]: Empty row list.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = ingestion_run_id
        return []


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
