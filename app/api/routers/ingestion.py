"""Ingestion API router composition for trigger and run diagnostics endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from app.config import AppSettings
from app.db import IngestionRunAlreadyActiveError, IngestionRunRecord, IngestionRunRepositoryPort
from app.jobs import JobOrchestratorPort, job_extract_missing_sections_from_diagnostics


def api_create_ingestion_router(
    settings: AppSettings,
    ingestion_repository: IngestionRunRepositoryPort,
    ingestion_orchestrator: JobOrchestratorPort,
    reprocess_orchestrator: JobOrchestratorPort | None = None,
) -> APIRouter:
    """Create ingestion router with trigger and run list/detail endpoints.

    Args:
        settings: Runtime settings used for pagination defaults.
        ingestion_repository: DB-layer ingestion run repository.
        ingestion_orchestrator: Job orchestrator for ingestion trigger execution.
        reprocess_orchestrator: Optional job orchestrator for reprocess trigger execution.

    Returns:
        APIRouter: Router exposing ingestion APIs.

    Raises:
        ValueError: Raised when dependencies are invalid.
    """

    if settings is None:
        raise ValueError("settings must not be None")
    if ingestion_repository is None:
        raise ValueError("ingestion_repository must not be None")
    if ingestion_orchestrator is None:
        raise ValueError("ingestion_orchestrator must not be None")

    router = APIRouter(prefix="/ingestion", tags=["ingestion"])

    @router.post("/run")
    def api_ingestion_run_trigger() -> JSONResponse:
        """Trigger one ingestion run via orchestrator.

        Returns:
            JSONResponse: Trigger result payload.

        Raises:
            RuntimeError: Raised when execution fails unexpectedly.
        """

        try:
            execution_result = ingestion_orchestrator.job_execute(job_name="ingestion_run")
            payload = {
                "job_name": execution_result.job_name,
                "status": execution_result.status,
            }
            return JSONResponse(content=payload, status_code=status.HTTP_200_OK)
        except IngestionRunAlreadyActiveError:
            payload = {
                "status": "error",
                "message": "run already active",
            }
            return JSONResponse(content=payload, status_code=status.HTTP_409_CONFLICT)

    @router.post("/reprocess")
    def api_ingestion_reprocess_trigger() -> JSONResponse:
        """Trigger one canonical reprocess run via orchestrator.

        Returns:
            JSONResponse: Trigger result payload.

        Raises:
            RuntimeError: Raised when execution fails unexpectedly.
        """

        target_orchestrator = reprocess_orchestrator or ingestion_orchestrator
        try:
            execution_result = target_orchestrator.job_execute(job_name="reprocess_run")
            payload = {
                "job_name": execution_result.job_name,
                "status": execution_result.status,
            }
            return JSONResponse(content=payload, status_code=status.HTTP_200_OK)
        except IngestionRunAlreadyActiveError:
            payload = {
                "status": "error",
                "message": "run already active",
            }
            return JSONResponse(content=payload, status_code=status.HTTP_409_CONFLICT)

    @router.get("/runs")
    def api_ingestion_run_list(
        limit: int = Query(default=settings.api_default_limit, ge=1),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        """Return ingestion runs list ordered by latest first.

        Args:
            limit: Max rows to return.
            offset: Rows to skip.

        Returns:
            JSONResponse: Runs list payload.

        Raises:
            RuntimeError: Raised when repository read fails.
        """

        run_rows = ingestion_repository.db_ingestion_run_list(limit=limit, offset=offset)
        payload = {
            "items": [api_serialize_ingestion_run_record(run_record) for run_record in run_rows],
            "page": {
                "limit": limit,
                "offset": offset,
                "returned": len(run_rows),
            },
        }
        return JSONResponse(content=payload, status_code=status.HTTP_200_OK)

    @router.get("/runs/{ingestion_run_id}")
    def api_ingestion_run_detail(ingestion_run_id: UUID) -> JSONResponse:
        """Return one ingestion run detail payload.

        Args:
            ingestion_run_id: Ingestion run identifier.

        Returns:
            JSONResponse: Run detail payload or 404 when absent.

        Raises:
            RuntimeError: Raised when repository read fails.
        """

        run_record = ingestion_repository.db_ingestion_run_get_by_id(ingestion_run_id=ingestion_run_id)
        if run_record is None:
            payload = {
                "status": "error",
                "message": "ingestion run not found",
            }
            return JSONResponse(content=payload, status_code=status.HTTP_404_NOT_FOUND)

        return JSONResponse(
            content=api_serialize_ingestion_run_record(run_record),
            status_code=status.HTTP_200_OK,
        )

    @router.get("/runs/{ingestion_run_id}/missing-sections")
    def api_ingestion_run_missing_sections(ingestion_run_id: UUID) -> JSONResponse:
        """Return extracted missing-section diagnostics for one ingestion run.

        Args:
            ingestion_run_id: Ingestion run identifier.

        Returns:
            JSONResponse: Missing-section diagnostics payload or 404 when absent.

        Raises:
            RuntimeError: Raised when repository read fails.
        """

        run_record = ingestion_repository.db_ingestion_run_get_by_id(ingestion_run_id=ingestion_run_id)
        if run_record is None:
            payload = {
                "status": "error",
                "message": "ingestion run not found",
            }
            return JSONResponse(content=payload, status_code=status.HTTP_404_NOT_FOUND)

        missing_sections_payload = job_extract_missing_sections_from_diagnostics(
            diagnostics=run_record.state.diagnostics,
        )
        payload = {
            "ingestion_run_id": str(run_record.ingestion_run_id),
            "status": run_record.state.status,
            "error_code": run_record.state.error_code,
            "missing_sections": missing_sections_payload["missing_sections"],
            "missing_hard_required": missing_sections_payload["missing_hard_required"],
            "missing_reconciliation_required": missing_sections_payload["missing_reconciliation_required"],
        }
        return JSONResponse(content=payload, status_code=status.HTTP_200_OK)

    return router


def api_serialize_ingestion_run_record(run_record: IngestionRunRecord) -> dict[str, object]:
    """Serialize typed ingestion run row to JSON response payload.

    Args:
        run_record: Typed ingestion run record.

    Returns:
        dict[str, object]: JSON-serializable ingestion run payload.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    return {
        "ingestion_run_id": str(run_record.ingestion_run_id),
        "account_id": run_record.account_id,
        "run_type": run_record.run_type,
        "status": run_record.state.status,
        "period_key": run_record.reference.period_key,
        "flex_query_id": run_record.reference.flex_query_id,
        "report_date_local": run_record.reference.report_date_local.isoformat()
        if run_record.reference.report_date_local
        else None,
        "started_at_utc": run_record.state.started_at_utc.isoformat(),
        "ended_at_utc": run_record.state.ended_at_utc.isoformat() if run_record.state.ended_at_utc else None,
        "duration_ms": run_record.state.duration_ms,
        "error_code": run_record.state.error_code,
        "error_message": run_record.state.error_message,
        "diagnostics": run_record.state.diagnostics,
        "created_at_utc": run_record.created_at_utc.isoformat(),
    }
