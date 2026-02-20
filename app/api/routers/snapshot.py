"""Snapshot API router composition for Task 7 daily snapshot reads."""
# pylint: disable=duplicate-code

from __future__ import annotations

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from app.config import AppSettings
from app.db import LedgerSnapshotRepositoryPort, PnlSnapshotDailyRecord


def api_create_snapshot_router(
    settings: AppSettings,
    snapshot_repository: LedgerSnapshotRepositoryPort,
) -> APIRouter:
    """Create snapshot router exposing daily snapshot read APIs.

    Args:
        settings: Runtime settings used for pagination defaults.
        snapshot_repository: DB-layer snapshot repository.

    Returns:
        APIRouter: Router exposing snapshot endpoints.

    Raises:
        ValueError: Raised when dependencies are invalid.
    """

    if settings is None:
        raise ValueError("settings must not be None")
    if snapshot_repository is None:
        raise ValueError("snapshot_repository must not be None")

    router = APIRouter(prefix="/snapshots", tags=["snapshots"])

    @router.get("/daily")
    def api_snapshot_daily_list(
        limit: int = Query(default=settings.api_default_limit, ge=1),
        offset: int = Query(default=0, ge=0),
        sort_by: str = Query(default="report_date_local"),
        sort_dir: str = Query(default="desc"),
        report_date_from: str | None = Query(default=None),
        report_date_to: str | None = Query(default=None),
    ) -> JSONResponse:
        """List Task 7 daily snapshot rows.

        Args:
            limit: Max rows to return.
            offset: Rows to skip.
            sort_by: Sort field.
            sort_dir: Sort direction.
            report_date_from: Optional inclusive lower report-date bound.
            report_date_to: Optional inclusive upper report-date bound.

        Returns:
            JSONResponse: Snapshot list envelope payload.

        Raises:
            RuntimeError: Raised when repository read fails.
        """

        normalized_sort_by = sort_by.strip()
        normalized_sort_dir = sort_dir.strip().lower()
        allowed_sort_by = {"report_date_local", "instrument_id", "total_pnl", "created_at_utc"}
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
        snapshot_rows = snapshot_repository.db_pnl_snapshot_daily_list(
            account_id=settings.account_id,
            limit=applied_limit,
            offset=offset,
            sort_by=normalized_sort_by,
            sort_dir=normalized_sort_dir,
            report_date_from=report_date_from,
            report_date_to=report_date_to,
        )

        payload = {
            "items": [api_serialize_pnl_snapshot_daily_row(snapshot_row) for snapshot_row in snapshot_rows],
            "page": {
                "limit": limit,
                "applied_limit": applied_limit,
                "offset": offset,
                "returned": len(snapshot_rows),
            },
            "sort": {
                "sort_by": normalized_sort_by,
                "sort_dir": normalized_sort_dir,
            },
            "filters": {
                "report_date_from": report_date_from,
                "report_date_to": report_date_to,
            },
        }
        return JSONResponse(content=payload, status_code=status.HTTP_200_OK)

    return router



def api_serialize_pnl_snapshot_daily_row(snapshot_row: PnlSnapshotDailyRecord) -> dict[str, object]:
    """Serialize one typed daily snapshot row to JSON payload.

    Args:
        snapshot_row: Typed daily snapshot row.

    Returns:
        dict[str, object]: JSON-serializable snapshot payload.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    return {
        "pnl_snapshot_daily_id": str(snapshot_row.pnl_snapshot_daily_id),
        "account_id": snapshot_row.account_id,
        "report_date_local": snapshot_row.report_date_local.isoformat(),
        "instrument_id": str(snapshot_row.instrument_id),
        "position_qty": snapshot_row.position_qty,
        "cost_basis": snapshot_row.cost_basis,
        "realized_pnl": snapshot_row.realized_pnl,
        "unrealized_pnl": snapshot_row.unrealized_pnl,
        "total_pnl": snapshot_row.total_pnl,
        "fees": snapshot_row.fees,
        "withholding_tax": snapshot_row.withholding_tax,
        "currency": snapshot_row.currency,
        "provisional": snapshot_row.provisional,
        "valuation_source": snapshot_row.valuation_source,
        "fx_source": snapshot_row.fx_source,
        "ingestion_run_id": None if snapshot_row.ingestion_run_id is None else str(snapshot_row.ingestion_run_id),
        "created_at_utc": snapshot_row.created_at_utc.isoformat(),
    }


__all__ = ["api_create_snapshot_router", "api_serialize_pnl_snapshot_daily_row"]
