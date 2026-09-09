"""Corporate-action manual case API."""

from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.db import CorporateActionManualCaseRecord, PortfolioRepositoryPort
from app.db.corporate_action_correction import SQLAlchemySplitCorrectionService, SplitCorrectionConflict


class ManualCaseUpdatePayload(BaseModel):
    status: str
    owner: str | None = None
    resolution_note: str | None = None


class SplitRatioPayload(BaseModel):
    new_shares: Decimal = Field(gt=0, max_digits=18, decimal_places=8, allow_inf_nan=False)
    old_shares: Decimal = Field(gt=0, max_digits=18, decimal_places=8, allow_inf_nan=False)
    note: str = Field(min_length=1, max_length=2000)


class SplitApplyPayload(SplitRatioPayload):
    preview_token: str = Field(min_length=64, max_length=64)


def api_create_corporate_action_router(
    repository: PortfolioRepositoryPort,
    correction_service: SQLAlchemySplitCorrectionService | None = None,
) -> APIRouter:
    """Create manual case list and resolution endpoints."""

    router = APIRouter(prefix="/corporate-actions", tags=["corporate-actions"])

    def correct_split(case_id: UUID, payload: SplitRatioPayload, token: str | None = None) -> JSONResponse:
        if correction_service is None:
            return JSONResponse({"message": "Split correction is not configured."}, status_code=503)
        try:
            return JSONResponse(correction_service.preview_or_apply(
                case_id, payload.new_shares, payload.old_shares, payload.note, token,
            ))
        except LookupError as error:
            return JSONResponse({"message": str(error)}, status_code=404)
        except SplitCorrectionConflict as error:
            return JSONResponse({"message": str(error)}, status_code=409)
        except ValueError as error:
            return JSONResponse({"message": str(error)}, status_code=400)
        except ArithmeticError:
            return JSONResponse({"message": "The ratio exceeds supported accounting precision; no changes were saved."}, status_code=400)
        except (RuntimeError, SQLAlchemyError):
            return JSONResponse({"message": "Correction failed; no changes were saved."}, status_code=500)

    @router.post("/cases/{case_id}/split/preview")
    def split_preview(case_id: UUID, payload: SplitRatioPayload) -> JSONResponse:
        return correct_split(case_id, payload)

    @router.post("/cases/{case_id}/split/apply")
    def split_apply(case_id: UUID, payload: SplitApplyPayload) -> JSONResponse:
        return correct_split(case_id, payload, payload.preview_token)

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
    if not row.requires_manual:
        reason = "This action has been handled by automatic processing or an applied correction."
        required_check = "Check current holdings against the broker statement; this case is retained for its review history."
    elif row.action_type in {"FORWARDSPLIT", "REVERSESPLIT", "STOCKDIV"}:
        reason = "A reliable adjustment ratio or unique action identity could not be established."
        required_check = (
            "Enter the new and old share quantities from the broker notice, preview the changes, then apply the verified ratio."
            if row.action_id and row.correction_identity_valid
            else "Accounting support required: this source has no consistent, unique broker action and security identity."
        )
    elif row.action_type == "CASHDIV":
        reason = "The cash payment has not been unambiguously matched to cash transactions."
        required_check = "Accounting support required: matching the payment and withholding to cash transactions is not implemented."
    else:
        reason = "This action cannot be accounted for automatically."
        required_check = "Accounting support required: this action needs security, quantity, cash and cost basis handling that the app does not yet support."
    return {
        "case_id": str(row.case_id), "event_corp_action_id": str(row.event_corp_action_id),
        "action_type": row.action_type, "instrument_id": str(row.instrument_id), "symbol": row.symbol,
        "status": row.status, "owner": row.owner, "resolution_note": row.resolution_note,
        "resolved_at_utc": None if row.resolved_at_utc is None else row.resolved_at_utc.isoformat(),
        "created_at_utc": row.created_at_utc.isoformat(), "updated_at_utc": row.updated_at_utc.isoformat(),
        "report_date_local": row.report_date_local.isoformat(), "description": row.description,
        "requires_manual": row.requires_manual, "review_reason": reason, "required_check": required_check,
        "can_correct_split": row.requires_manual and bool(row.action_id) and row.correction_identity_valid and row.action_type in {
            "FORWARDSPLIT", "REVERSESPLIT", "STOCKDIV",
        },
    }
