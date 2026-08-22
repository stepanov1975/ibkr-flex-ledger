"""Operational SLO visibility endpoints."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import AppSettings
from app.db import PortfolioRepositoryPort
from app.operations import operations_build_slo_status


def api_create_operations_router(settings: AppSettings, repository: PortfolioRepositoryPort) -> APIRouter:
    """Expose frozen ingestion reliability targets and alert state."""

    router = APIRouter(prefix="/operations", tags=["operations"])

    @router.get("/slo")
    def ingestion_slo() -> JSONResponse:
        now = datetime.now(timezone.utc)
        rows = repository.db_ingestion_slo_records(settings.account_id, now - timedelta(days=30))
        status = operations_build_slo_status(rows, measured_at_utc=now)
        return JSONResponse(content=status.api_payload())

    return router
