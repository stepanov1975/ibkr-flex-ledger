"""Regression tests for Task 7 snapshot read API endpoints."""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.application import create_api_application
from app.config import AppSettings
from app.db.interfaces import PnlSnapshotDailyRecord
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
    """Minimal ingestion repository stub for app factory dependencies."""

    def db_ingestion_run_list(self, limit: int, offset: int, sort_by: str = "started_at_utc", sort_dir: str = "desc"):
        """Return empty run list.

        Args:
            limit: Max rows to return.
            offset: Rows to skip.
            sort_by: Sort field.
            sort_dir: Sort direction.

        Returns:
            list: Empty list.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = (limit, offset, sort_by, sort_dir)
        return []

    def db_ingestion_run_get_by_id(self, ingestion_run_id):
        """Return no run record.

        Args:
            ingestion_run_id: Target run id.

        Returns:
            None: This stub always returns no record.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = ingestion_run_id
        return None


class _SuccessOrchestrator:
    """Minimal orchestrator stub for app factory dependencies."""

    def job_supported_names(self) -> tuple[str, ...]:
        """Return supported names.

        Returns:
            tuple[str, ...]: Supported names tuple.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return ("ingestion_run",)

    def job_execute(self, job_name: str):
        """Return deterministic execution success payload.

        Args:
            job_name: Requested job name.

        Returns:
            object: Lightweight success result.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return type("Result", (), {"job_name": job_name, "status": "success"})()


class _SnapshotRepositoryStub:
    """Snapshot repository stub capturing list call arguments."""

    def __init__(self) -> None:
        """Initialize stub state.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.calls: list[dict[str, object]] = []

    def db_pnl_snapshot_daily_list(
        self,
        account_id: str,
        limit: int,
        offset: int,
        sort_by: str,
        sort_dir: str,
        report_date_from: str | None = None,
        report_date_to: str | None = None,
    ) -> list[PnlSnapshotDailyRecord]:
        """Return deterministic one-row daily snapshot payload.

        Args:
            account_id: Internal account id.
            limit: Max rows.
            offset: Rows to skip.
            sort_by: Sort field.
            sort_dir: Sort direction.
            report_date_from: Optional lower date bound.
            report_date_to: Optional upper date bound.

        Returns:
            list[PnlSnapshotDailyRecord]: Singleton snapshot row.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.calls.append(
            {
                "account_id": account_id,
                "limit": limit,
                "offset": offset,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
                "report_date_from": report_date_from,
                "report_date_to": report_date_to,
            }
        )
        return [
            PnlSnapshotDailyRecord(
                pnl_snapshot_daily_id=uuid4(),
                account_id=account_id,
                report_date_local=date(2026, 2, 20),
                instrument_id=uuid4(),
                position_qty="6",
                cost_basis="600",
                realized_pnl="78.6",
                unrealized_pnl="179.4",
                total_pnl="258.0",
                fees="2.0",
                withholding_tax="0",
                currency="USD",
                provisional=False,
                valuation_source="trade_price_fallback",
                fx_source="event_fx_fallback",
                ingestion_run_id=None,
                created_at_utc=datetime.now(timezone.utc),
            )
        ]



def _build_settings() -> AppSettings:
    """Build deterministic app settings for snapshot API tests.

    Returns:
        AppSettings: Test settings.

    Raises:
        ValueError: Raised when settings are invalid.
    """

    return AppSettings(
        environment_name="test-api-snapshot",
        database_url="postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
        account_id="U_TEST",
        ibkr_flex_token="token",
        ibkr_flex_query_id="query",
        api_default_limit=25,
        api_max_limit=200,
    )


def test_api_snapshot_daily_list_returns_contract_envelope() -> None:
    """Return Task 7 snapshot rows using list-envelope contract.

    Returns:
        None: Assertions validate API payload contract.

    Raises:
        AssertionError: Raised when list contract behavior deviates.
    """

    snapshot_repository = _SnapshotRepositoryStub()
    application = create_api_application(
        settings=_build_settings(),
        db_health_service=_HealthyDatabaseService(),
        ingestion_repository=_IngestionRepositoryStub(),
        ingestion_orchestrator=_SuccessOrchestrator(),
        snapshot_repository=snapshot_repository,
    )
    client = TestClient(application)

    response = client.get(
        "/snapshots/daily",
        params={
            "limit": 500,
            "offset": 5,
            "sort_by": "report_date_local",
            "sort_dir": "desc",
            "report_date_from": "2026-02-01",
            "report_date_to": "2026-02-28",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == {"limit": 500, "applied_limit": 200, "offset": 5, "returned": 1}
    assert payload["sort"] == {"sort_by": "report_date_local", "sort_dir": "desc"}
    assert payload["filters"] == {"report_date_from": "2026-02-01", "report_date_to": "2026-02-28"}
    assert payload["items"][0]["realized_pnl"] == "78.6"
    assert snapshot_repository.calls[0]["account_id"] == "U_TEST"


def test_api_snapshot_daily_list_rejects_invalid_sort_field() -> None:
    """Return deterministic validation payload for unsupported sort field.

    Returns:
        None: Assertions validate error contract.

    Raises:
        AssertionError: Raised when invalid sort is not rejected.
    """

    application = create_api_application(
        settings=_build_settings(),
        db_health_service=_HealthyDatabaseService(),
        ingestion_repository=_IngestionRepositoryStub(),
        ingestion_orchestrator=_SuccessOrchestrator(),
        snapshot_repository=_SnapshotRepositoryStub(),
    )
    client = TestClient(application)

    response = client.get("/snapshots/daily", params={"sort_by": "status"})

    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "INVALID_SORT_FIELD"
