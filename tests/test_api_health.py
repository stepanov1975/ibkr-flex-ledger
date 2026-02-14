"""Tests for API health endpoint behavior.

These tests validate deterministic response behavior for healthy and
database-unavailable states.
"""

from fastapi.testclient import TestClient

from app.api.application import create_api_application
from app.config import AppSettings
from app.domain import HealthStatus


class _HealthyDatabaseService:
    """Test double that simulates a healthy database target."""

    def db_connection_label(self) -> str:
        """Return deterministic target label.

        Returns:
            str: Health target label.

        Raises:
            RuntimeError: Never raised by this test double.
        """

        return "postgresql://test"

    def db_check_health(self) -> HealthStatus:
        """Return healthy database result.

        Returns:
            HealthStatus: Healthy DB response.

        Raises:
            ConnectionError: Never raised by this test double.
        """

        return HealthStatus(status="ok", detail="database connectivity verified")


class _FailingDatabaseService:
    """Test double that simulates a database connectivity failure."""

    def db_connection_label(self) -> str:
        """Return deterministic target label.

        Returns:
            str: Health target label.

        Raises:
            RuntimeError: Never raised by this test double.
        """

        return "postgresql://test"

    def db_check_health(self) -> HealthStatus:
        """Raise deterministic connection error.

        Returns:
            HealthStatus: This method does not return.

        Raises:
            ConnectionError: Always raised by this test double.
        """

        raise ConnectionError("database connectivity check failed")


class _IngestionRepositoryStub:
    """Minimal repository stub for API factory dependency injection."""

    def db_ingestion_run_list(
        self,
        _limit: int,
        _offset: int,
        sort_by: str = "started_at_utc",
        sort_dir: str = "desc",
    ) -> list[object]:
        """Return deterministic empty run list.

        Args:
            limit: Max rows.
            offset: Rows to skip.

        Returns:
            list[object]: Empty list for health tests.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = (sort_by, sort_dir)
        return []

    def db_ingestion_run_get_by_id(self, _ingestion_run_id) -> None:
        """Return no run for health tests.

        Args:
            ingestion_run_id: Run id.

        Returns:
            None: Always returns no run.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return None


class _IngestionOrchestratorStub:
    """Minimal orchestrator stub for API factory dependency injection."""

    def job_supported_names(self) -> tuple[str, ...]:
        """Return supported stub job names.

        Returns:
            tuple[str, ...]: Stub job names tuple.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return ("ingestion_run",)

    def job_execute(self, job_name: str):
        """Return deterministic success-like object.

        Args:
            job_name: Job name.

        Returns:
            object: Result object with status fields.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return type("Result", (), {"job_name": job_name, "status": "success"})()


def _build_settings() -> AppSettings:
    """Create test settings object.

    Returns:
        AppSettings: Deterministic test settings for API creation.

    Raises:
        ValueError: Raised by AppSettings when values are invalid.
    """

    return AppSettings(
        environment_name="test",
        database_url="postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        ibkr_flex_token="token",
        ibkr_flex_query_id="query",
    )


def test_api_health_returns_success_when_database_is_available() -> None:
    """Return HTTP 200 and healthy payload when DB service reports success.

    Returns:
        None: Assertions validate response behavior.

    Raises:
        AssertionError: Raised when response does not match expected payload.
    """

    application = create_api_application(
        _build_settings(),
        _HealthyDatabaseService(),
        _IngestionRepositoryStub(),
        _IngestionOrchestratorStub(),
    )
    client = TestClient(application)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["app"] == "up"
    assert response.json()["database"] == "ok"


def test_api_health_returns_service_unavailable_when_database_is_down() -> None:
    """Return HTTP 503 and degraded payload when DB service reports failure.

    Returns:
        None: Assertions validate response behavior.

    Raises:
        AssertionError: Raised when response does not match expected payload.
    """

    application = create_api_application(
        _build_settings(),
        _FailingDatabaseService(),
        _IngestionRepositoryStub(),
        _IngestionOrchestratorStub(),
    )
    client = TestClient(application)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["app"] == "up"
    assert response.json()["database"] == "down"
