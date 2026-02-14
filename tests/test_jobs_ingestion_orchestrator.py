"""Regression tests for ingestion orchestrator lifecycle transitions."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.adapters import AdapterFetchResult
from app.db.interfaces import IngestionRunRecord, IngestionRunReference, IngestionRunState
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
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository_stub,
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
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=repository_stub,
        flex_adapter=adapter_stub,
        config=IngestionOrchestratorConfig(account_id="U_TEST", flex_query_id="query"),
    )

    result = orchestrator.job_execute(job_name="ingestion_run")

    assert result.status == "success"
    assert repository_stub.finalize_calls[0]["status"] == "success"
    diagnostics = repository_stub.finalize_calls[0]["diagnostics"]
    assert isinstance(diagnostics, list)
    assert any(event["stage"] == "persist" for event in diagnostics)
