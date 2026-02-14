"""Regression tests for ingestion API trigger and run detail/list behavior."""
# pylint: disable=duplicate-code

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.application import create_api_application
from app.config import AppSettings
from app.db import IngestionRunAlreadyActiveError
from app.db.interfaces import IngestionRunRecord, IngestionRunReference, IngestionRunState
from app.domain import HealthStatus


class _HealthyDatabaseService:
    """Database health stub for API tests."""

    def db_connection_label(self) -> str:
        """Return deterministic label.

        Returns:
            str: Label string.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return "postgresql://test"

    def db_check_health(self) -> HealthStatus:
        """Return healthy response.

        Returns:
            HealthStatus: Healthy status payload.

        Raises:
            ConnectionError: This stub does not raise connection errors.
        """

        return HealthStatus(status="ok", detail="database connectivity verified")


class _IngestionRepositoryStub:
    """In-memory ingestion repository stub for API tests."""

    def __init__(self, run_record: IngestionRunRecord):
        """Initialize repository stub.

        Args:
            run_record: Deterministic run record returned by detail endpoint.

        Returns:
            None: Initializer does not return values.

        Raises:
            ValueError: This stub does not raise value errors.
        """

        self._run_record = run_record

    def db_ingestion_run_list(self, limit: int, offset: int) -> list[IngestionRunRecord]:
        """Return deterministic singleton list for list endpoint.

        Args:
            limit: Max rows to return.
            offset: Rows to skip.

        Returns:
            list[IngestionRunRecord]: Singleton run list.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        if limit < 1 or offset < 0:
            return []
        return [self._run_record]

    def db_ingestion_run_get_by_id(self, ingestion_run_id) -> IngestionRunRecord | None:
        """Return one run when identifier matches.

        Args:
            ingestion_run_id: Target run id.

        Returns:
            IngestionRunRecord | None: Matching run or None.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        if ingestion_run_id == self._run_record.ingestion_run_id:
            return self._run_record
        return None


class _ConflictIngestionOrchestrator:
    """Orchestrator stub that simulates active-run conflict."""

    def job_supported_names(self) -> tuple[str, ...]:
        """Return supported stub job names.

        Returns:
            tuple[str, ...]: Supported job names tuple.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return ("ingestion_run",)

    def job_execute(self, job_name: str):
        """Always raise active run conflict.

        Args:
            job_name: Requested job name.

        Returns:
            object: This method does not return.

        Raises:
            IngestionRunAlreadyActiveError: Always raised.
        """

        _ = job_name
        raise IngestionRunAlreadyActiveError("run already active")


class _SuccessReprocessOrchestrator:
    """Orchestrator stub that simulates successful reprocess execution."""

    def job_supported_names(self) -> tuple[str, ...]:
        """Return supported stub job names.

        Returns:
            tuple[str, ...]: Supported job names tuple.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return ("reprocess_run",)

    def job_execute(self, job_name: str):
        """Return successful reprocess execution response.

        Args:
            job_name: Requested job name.

        Returns:
            object: Lightweight result object.

        Raises:
            RuntimeError: This method does not raise runtime errors.
        """

        return type("Result", (), {"job_name": job_name, "status": "success"})()


def _build_success_ingestion_orchestrator() -> object:
    """Build success orchestrator object for API tests.

    Returns:
        object: Orchestrator-like object with required methods.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    class _SuccessOrchestrator:
        """Local success orchestrator type for API tests."""

        def job_supported_names(self) -> tuple[str, ...]:
            """Return supported job names.

            Returns:
                tuple[str, ...]: Supported names.

            Raises:
                RuntimeError: This method does not raise runtime errors.
            """

            return ("ingestion_run",)

        def job_execute(self, job_name: str):
            """Return successful execution response.

            Args:
                job_name: Requested job name.

            Returns:
                object: Lightweight result object.

            Raises:
                RuntimeError: This method does not raise runtime errors.
            """

            return type("Result", (), {"job_name": job_name, "status": "success"})()

    return _SuccessOrchestrator()


def _build_settings() -> AppSettings:
    """Build deterministic app settings for API tests.

    Returns:
        AppSettings: Test settings.

    Raises:
        ValueError: Raised when settings are invalid.
    """

    return AppSettings(
        environment_name="test-api-ingestion",
        database_url="postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        account_id="U_TEST",
        ibkr_flex_token="token",
        ibkr_flex_query_id="query",
        api_default_limit=25,
        api_max_limit=200,
    )


def _build_run_record() -> IngestionRunRecord:
    """Build deterministic ingestion run record.

    Returns:
        IngestionRunRecord: Typed run record.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    run_id = uuid4()
    started_at = datetime.now(timezone.utc)
    return IngestionRunRecord(
        ingestion_run_id=run_id,
        account_id="U_TEST",
        run_type="manual",
        reference=IngestionRunReference(
            period_key=date.today().isoformat(),
            flex_query_id="query",
            report_date_local=None,
        ),
        state=IngestionRunState(
            status="failed",
            started_at_utc=started_at,
            ended_at_utc=started_at,
            duration_ms=123,
            error_code="MISSING_REQUIRED_SECTION",
            error_message="missing required sections",
            diagnostics=[
                {
                    "stage": "preflight",
                    "status": "failed",
                    "error_code": "MISSING_REQUIRED_SECTION",
                    "missing_sections": ["OpenPositions", "CashTransactions"],
                    "missing_hard_required": ["OpenPositions", "CashTransactions"],
                    "missing_reconciliation_required": [],
                }
            ],
        ),
        created_at_utc=started_at,
    )


def _build_run_record_with_canonical_diagnostics() -> IngestionRunRecord:
    """Build deterministic successful run record with canonical diagnostics.

    Returns:
        IngestionRunRecord: Typed run record with canonical stage completion details.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    run_id = uuid4()
    started_at = datetime.now(timezone.utc)
    return IngestionRunRecord(
        ingestion_run_id=run_id,
        account_id="U_TEST",
        run_type="manual",
        reference=IngestionRunReference(
            period_key=date.today().isoformat(),
            flex_query_id="query",
            report_date_local=None,
        ),
        state=IngestionRunState(
            status="success",
            started_at_utc=started_at,
            ended_at_utc=started_at,
            duration_ms=200,
            error_code=None,
            error_message=None,
            diagnostics=[
                {
                    "stage": "canonical_mapping",
                    "status": "completed",
                    "details": {
                        "canonical_input_row_count": 14946,
                        "canonical_duration_ms": 1948,
                        "canonical_skip_reason": None,
                    },
                }
            ],
        ),
        created_at_utc=started_at,
    )


def test_api_ingestion_trigger_returns_409_when_run_already_active() -> None:
    """Return HTTP 409 when ingestion trigger collides with active run lock.

    Returns:
        None: Assertions validate behavior.

    Raises:
        AssertionError: Raised when response does not match expected values.
    """

    run_record = _build_run_record()
    application = create_api_application(
        settings=_build_settings(),
        db_health_service=_HealthyDatabaseService(),
        ingestion_repository=_IngestionRepositoryStub(run_record=run_record),
        ingestion_orchestrator=_ConflictIngestionOrchestrator(),
    )
    client = TestClient(application)

    response = client.post("/ingestion/run")

    assert response.status_code == 409
    assert response.json()["message"] == "run already active"


def test_api_ingestion_run_detail_includes_status_and_timeline() -> None:
    """Return run detail payload including status and diagnostics timeline.

    Returns:
        None: Assertions validate detail payload shape.

    Raises:
        AssertionError: Raised when response payload is missing required fields.
    """

    run_record = _build_run_record()
    application = create_api_application(
        settings=_build_settings(),
        db_health_service=_HealthyDatabaseService(),
        ingestion_repository=_IngestionRepositoryStub(run_record=run_record),
        ingestion_orchestrator=_build_success_ingestion_orchestrator(),
    )
    client = TestClient(application)

    response = client.get(f"/ingestion/runs/{run_record.ingestion_run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert isinstance(payload["diagnostics"], list)
    assert payload["diagnostics"][0]["stage"] == "preflight"


def test_api_ingestion_missing_sections_endpoint_returns_exact_sections() -> None:
    """Return extracted missing section details for failed preflight runs.

    Returns:
        None: Assertions validate payload values.

    Raises:
        AssertionError: Raised when payload does not include expected section names.
    """

    run_record = _build_run_record()
    application = create_api_application(
        settings=_build_settings(),
        db_health_service=_HealthyDatabaseService(),
        ingestion_repository=_IngestionRepositoryStub(run_record=run_record),
        ingestion_orchestrator=_build_success_ingestion_orchestrator(),
    )
    client = TestClient(application)

    response = client.get(f"/ingestion/runs/{run_record.ingestion_run_id}/missing-sections")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error_code"] == "MISSING_REQUIRED_SECTION"
    assert "OpenPositions" in payload["missing_sections"]


def test_api_reprocess_trigger_returns_success() -> None:
    """Return HTTP 200 for successful reprocess trigger execution.

    Returns:
        None: Assertions validate reprocess trigger behavior.

    Raises:
        AssertionError: Raised when response does not match expected values.
    """

    run_record = _build_run_record()
    application = create_api_application(
        settings=_build_settings(),
        db_health_service=_HealthyDatabaseService(),
        ingestion_repository=_IngestionRepositoryStub(run_record=run_record),
        ingestion_orchestrator=_build_success_ingestion_orchestrator(),
        reprocess_orchestrator=_SuccessReprocessOrchestrator(),
    )
    client = TestClient(application)

    response = client.post("/ingestion/reprocess")

    assert response.status_code == 200
    assert response.json()["job_name"] == "reprocess_run"
    assert response.json()["status"] == "success"


def test_api_ingestion_run_detail_includes_canonical_summary_fields() -> None:
    """Return canonical summary fields on run detail payload.

    Returns:
        None: Assertions validate canonical summary projection.

    Raises:
        AssertionError: Raised when canonical fields are missing.
    """

    run_record = _build_run_record_with_canonical_diagnostics()
    application = create_api_application(
        settings=_build_settings(),
        db_health_service=_HealthyDatabaseService(),
        ingestion_repository=_IngestionRepositoryStub(run_record=run_record),
        ingestion_orchestrator=_build_success_ingestion_orchestrator(),
    )
    client = TestClient(application)

    response = client.get(f"/ingestion/runs/{run_record.ingestion_run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["canonical_input_row_count"] == 14946
    assert payload["canonical_duration_ms"] == 1948
    assert payload["canonical_skip_reason"] is None


def test_api_ingestion_run_list_includes_canonical_summary_fields() -> None:
    """Return canonical summary fields on run list payload items.

    Returns:
        None: Assertions validate canonical summary projection.

    Raises:
        AssertionError: Raised when canonical fields are missing on list items.
    """

    run_record = _build_run_record_with_canonical_diagnostics()
    application = create_api_application(
        settings=_build_settings(),
        db_health_service=_HealthyDatabaseService(),
        ingestion_repository=_IngestionRepositoryStub(run_record=run_record),
        ingestion_orchestrator=_build_success_ingestion_orchestrator(),
    )
    client = TestClient(application)

    response = client.get("/ingestion/runs")

    assert response.status_code == 200
    payload = response.json()["items"][0]
    assert payload["canonical_input_row_count"] == 14946
    assert payload["canonical_duration_ms"] == 1948
    assert payload["canonical_skip_reason"] is None
