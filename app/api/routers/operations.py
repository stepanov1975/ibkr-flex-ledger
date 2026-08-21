"""Operational SLO visibility endpoints."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.analytics import analytics_ingestion_slo_summary
from app.config import AppSettings
from app.db import PortfolioRepositoryPort


def api_create_operations_router(settings: AppSettings, repository: PortfolioRepositoryPort) -> APIRouter:
    """Expose frozen ingestion reliability targets and alert state."""

    router = APIRouter(prefix="/operations", tags=["operations"])

    @router.get("/slo")
    def ingestion_slo() -> JSONResponse:
        now = datetime.now(timezone.utc)
        rows = repository.db_ingestion_slo_records(settings.account_id, now - timedelta(days=30))
        summary = analytics_ingestion_slo_summary(rows)
        alerting = summary.success_breached or summary.duration_breached or summary.consecutive_failure_alert
        return JSONResponse(
            content={
                "measured_at_utc": now.isoformat(),
                "window_days": 30,
                "run_count": summary.run_count,
                "success_count": summary.success_count,
                "success_rate": summary.success_rate,
                "success_target": summary.success_target,
                "success_alert_threshold": summary.success_alert_threshold,
                "success_breached": summary.success_breached,
                "p95_duration_ms": summary.p95_duration_ms,
                "p95_target_ms": summary.p95_target_ms,
                "duration_alert_threshold_ms": summary.duration_alert_threshold_ms,
                "duration_breached": summary.duration_breached,
                "consecutive_failure_alert": summary.consecutive_failure_alert,
                "alerting": alerting,
                "owner": "app owner",
            }
        )

    return router
