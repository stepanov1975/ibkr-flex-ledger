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
    def api_ingestion_reprocess_trigger(
        period_key: str | None = Query(default=None),
        flex_query_id: str | None = Query(default=None),
    ) -> JSONResponse:
        """Trigger one canonical reprocess run via orchestrator.

        Returns:
            JSONResponse: Trigger result payload.

        Raises:
            RuntimeError: Raised when execution fails unexpectedly.
        """

        target_orchestrator = reprocess_orchestrator or ingestion_orchestrator
        try:
            if period_key is not None or flex_query_id is not None:
                normalized_period_key = (period_key or "").strip()
                normalized_flex_query_id = (flex_query_id or "").strip()
                if not normalized_period_key:
                    payload = {
                        "status": "error",
                        "message": "period_key must not be blank when explicit scope is provided",
                    }
                    return JSONResponse(content=payload, status_code=status.HTTP_400_BAD_REQUEST)
                if not normalized_flex_query_id:
                    payload = {
                        "status": "error",
                        "message": "flex_query_id must not be blank when explicit scope is provided",
                    }
                    return JSONResponse(content=payload, status_code=status.HTTP_400_BAD_REQUEST)

                scoped_execute = getattr(target_orchestrator, "job_execute_reprocess_target", None)
                if scoped_execute is None:
                    payload = {
                        "status": "error",
                        "message": "configured reprocess orchestrator does not support explicit scope overrides",
                    }
                    return JSONResponse(content=payload, status_code=status.HTTP_400_BAD_REQUEST)
                execution_result = scoped_execute(
                    period_key=normalized_period_key,
                    flex_query_id=normalized_flex_query_id,
                )
            else:
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
        sort_by: str = Query(default="started_at_utc"),
        sort_dir: str = Query(default="desc"),
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

        normalized_sort_by = sort_by.strip()
        normalized_sort_dir = sort_dir.strip().lower()
        allowed_sort_by = {"started_at_utc", "ended_at_utc", "status", "duration_ms"}
        allowed_sort_dir = {"asc", "desc"}
        if normalized_sort_by not in allowed_sort_by:
            payload = {
                "status": "error",
                "code": "INVALID_SORT_FIELD",
                "message": f"unsupported sort_by={normalized_sort_by}",
            }
            return JSONResponse(content=payload, status_code=status.HTTP_400_BAD_REQUEST)
        if normalized_sort_dir not in allowed_sort_dir:
            payload = {
                "status": "error",
                "code": "INVALID_SORT_DIRECTION",
                "message": f"unsupported sort_dir={normalized_sort_dir}",
            }
            return JSONResponse(content=payload, status_code=status.HTTP_400_BAD_REQUEST)

        applied_limit = min(limit, settings.api_max_limit)
        run_rows = ingestion_repository.db_ingestion_run_list(
            limit=applied_limit,
            offset=offset,
            sort_by=normalized_sort_by,
            sort_dir=normalized_sort_dir,
        )
        payload = {
            "items": [api_serialize_ingestion_run_record(run_record) for run_record in run_rows],
            "page": {
                "limit": limit,
                "applied_limit": applied_limit,
                "offset": offset,
                "returned": len(run_rows),
            },
            "sort": {
                "sort_by": normalized_sort_by,
                "sort_dir": normalized_sort_dir,
            },
            "filters": {},
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

    canonical_details = _api_extract_canonical_mapping_details(run_record.state.diagnostics)

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
        "canonical_input_row_count": canonical_details.get("canonical_input_row_count"),
        "canonical_duration_ms": canonical_details.get("canonical_duration_ms"),
        "canonical_skip_reason": canonical_details.get("canonical_skip_reason"),
        "diagnostics": run_record.state.diagnostics,
        "created_at_utc": run_record.created_at_utc.isoformat(),
    }


def _api_extract_canonical_mapping_details(diagnostics: list[dict[str, object]] | None) -> dict[str, object]:
    """Extract canonical mapping completion details from run diagnostics timeline.

    Args:
        diagnostics: Optional run diagnostics timeline events.

    Returns:
        dict[str, object]: Canonical completion details or empty dict when unavailable.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    if diagnostics is None:
        return {}

    for event in reversed(diagnostics):
        if event.get("stage") != "canonical_mapping":
            continue
        if event.get("status") != "completed":
            continue

        details = event.get("details")
        if not isinstance(details, dict):
            return {}
        return details

    return {}
