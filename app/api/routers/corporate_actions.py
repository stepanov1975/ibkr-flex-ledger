"""Corporate-action manual case API."""

from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.db import CorporateActionManualCaseRecord, PortfolioRepositoryPort


class ManualCaseUpdatePayload(BaseModel):
    status: str
    owner: str | None = None
    resolution_note: str | None = None


def api_create_corporate_action_router(repository: PortfolioRepositoryPort) -> APIRouter:
    """Create manual case list and resolution endpoints."""

    router = APIRouter(prefix="/corporate-actions", tags=["corporate-actions"])

    @router.get("/cases")
    def case_list(case_status: str | None = Query(default=None, alias="status")) -> JSONResponse:
        if case_status is not None and case_status not in {"open", "resolved", "dismissed"}:
            return JSONResponse(
                content={"status": "error", "code": "INVALID_CASE_STATUS", "message": "unsupported status"},
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return JSONResponse(content={"items": [_case(row) for row in repository.db_manual_case_list(case_status)]})

    @router.patch("/cases/{case_id}")
    def case_update(case_id: UUID, payload: ManualCaseUpdatePayload) -> JSONResponse:
        try:
            row = repository.db_manual_case_update(case_id, payload.status, payload.owner, payload.resolution_note)
        except ValueError as error:
            return JSONResponse(
                content={"status": "error", "code": "INVALID_CASE_UPDATE", "message": str(error)},
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if row is None:
            return JSONResponse(
                content={"status": "error", "code": "NOT_FOUND", "message": "manual case not found"},
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return JSONResponse(content=_case(row))

    return router


def _case(row: CorporateActionManualCaseRecord) -> dict[str, object]:
    return {
        "case_id": str(row.case_id), "event_corp_action_id": str(row.event_corp_action_id),
        "action_type": row.action_type, "instrument_id": str(row.instrument_id), "symbol": row.symbol,
        "status": row.status, "owner": row.owner, "resolution_note": row.resolution_note,
        "resolved_at_utc": None if row.resolved_at_utc is None else row.resolved_at_utc.isoformat(),
        "created_at_utc": row.created_at_utc.isoformat(), "updated_at_utc": row.updated_at_utc.isoformat(),
    }
