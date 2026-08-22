"""PnL, provenance, and reconciliation report APIs with CSV v1 exports."""

from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from io import StringIO
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse, Response

from app.analytics import ReconciliationDiff, analytics_build_reconciliation_diffs
from app.config import AppSettings
from app.db import InstrumentPnlReportRecord, LabelPnlReportRecord, PortfolioRepositoryPort


_INSTRUMENT_COLUMNS = (
    "report_date_local", "instrument_id", "conid", "symbol", "currency", "position_qty", "cost_basis",
    "realized_pnl", "unrealized_pnl", "total_pnl", "provisional",
)
_LABEL_COLUMNS = (
    "report_date_local", "label_id", "label_name", "instrument_count", "realized_pnl", "unrealized_pnl",
    "total_pnl", "fees", "withholding_tax", "provisional",
)
_RECONCILIATION_COLUMNS = (
    "report_date_local", "instrument_id", "conid", "symbol", "metric", "broker_value", "economic_value",
    "abs_diff", "rel_diff", "tolerance_abs", "tolerance_rel", "within_tolerance", "formula_context",
    "source_event_id", "source_raw_record_id", "provisional",
)


def api_create_reports_router(settings: AppSettings, repository: PortfolioRepositoryPort) -> APIRouter:
    """Create stable JSON and CSV report endpoints."""

    router = APIRouter(prefix="/reports", tags=["reports"])

    @router.get("/pnl/by-instrument")
    def pnl_by_instrument(
        report_date_from: date | None = Query(default=None),
        report_date_to: date | None = Query(default=None),
        instrument_id: UUID | None = Query(default=None),
        output: str = Query(default="json", alias="format"),
    ) -> Response:
        invalid = _validate_report_query(report_date_from, report_date_to, output)
        if invalid is not None:
            return invalid
        rows = repository.db_report_pnl_by_instrument(
            settings.account_id, report_date_from, report_date_to, instrument_id
        )
        if output == "csv":
            return _csv_response("pnl-by-instrument-v1.csv", _INSTRUMENT_COLUMNS, [_instrument_csv(row) for row in rows])
        return JSONResponse(
            content={"schema_version": "v1", "items": [_instrument_json(row) for row in rows],
                     "filters": _filters(report_date_from, report_date_to, instrument_id)}
        )

    @router.get("/pnl/by-label")
    def pnl_by_label(
        report_date_from: date | None = Query(default=None),
        report_date_to: date | None = Query(default=None),
        label_id: UUID | None = Query(default=None),
        output: str = Query(default="json", alias="format"),
    ) -> Response:
        invalid = _validate_report_query(report_date_from, report_date_to, output)
        if invalid is not None:
            return invalid
        rows = repository.db_report_pnl_by_label(settings.account_id, report_date_from, report_date_to, label_id)
        if output == "csv":
            return _csv_response("pnl-by-label-v1.csv", _LABEL_COLUMNS, [_label_csv(row) for row in rows])
        return JSONResponse(
            content={"schema_version": "v1", "items": [_label_json(row) for row in rows],
                     "filters": _filters(report_date_from, report_date_to, label_id)}
        )

    @router.get("/portfolio-summary")
    def portfolio_summary() -> JSONResponse:
        summary = repository.db_report_portfolio_summary(settings.account_id)
        return JSONResponse(
            content={
                "schema_version": "v1",
                "report_date_local": (
                    None if summary.report_date_local is None else summary.report_date_local.isoformat()
                ),
                "cash_balances": [
                    {"currency": row.currency, "amount": row.amount} for row in summary.cash_balances
                ],
                "transfer_summary_by_currency": [
                    {
                        "currency": row.currency,
                        "net_transfers": row.net_transfers,
                        "gross_deposits": row.gross_deposits,
                        "gross_withdrawals": row.gross_withdrawals,
                    }
                    for row in summary.transfer_summary_by_currency
                ],
                "transfers": [
                    {
                        "report_date_local": row.report_date_local.isoformat(),
                        "type": row.transfer_type,
                        "amount": row.amount,
                        "currency": row.currency,
                        "description": row.description,
                    }
                    for row in summary.transfers
                ],
                "net_transfers_usd": summary.net_transfers_usd,
                "estimated_net_liquidation_value_usd": summary.estimated_net_liquidation_value_usd,
                "total_profit_usd": summary.total_profit_usd,
                "profit_percent": summary.profit_percent,
            }
        )

    @router.get("/provenance")
    def provenance(report_date_local: date, instrument_id: UUID) -> JSONResponse:
        rows = repository.db_report_provenance(settings.account_id, report_date_local, instrument_id)
        return JSONResponse(
            content={
                "report_date_local": report_date_local.isoformat(),
                "instrument_id": str(instrument_id),
                "items": [
                    {
                        "event_type": row.event_type,
                        "event_id": str(row.event_id),
                        "source_raw_record_id": str(row.source_raw_record_id),
                        "section_name": row.section_name,
                        "source_row_ref": row.source_row_ref,
                        "source_payload": row.source_payload,
                    }
                    for row in rows
                ],
            }
        )

    @router.get("/reconciliation/diff")
    def reconciliation_diff(
        report_date_from: date | None = Query(default=None),
        report_date_to: date | None = Query(default=None),
        instrument_id: UUID | None = Query(default=None),
        output: str = Query(default="json", alias="format"),
    ) -> Response:
        invalid = _validate_report_query(report_date_from, report_date_to, output)
        if invalid is not None:
            return invalid
        missing = repository.db_reconciliation_missing_sections(
            settings.account_id, report_date_from, report_date_to
        )
        if missing:
            return JSONResponse(
                content={"status": "error", "code": "MISSING_REQUIRED_SECTION", "missing_sections": missing},
                status_code=status.HTTP_409_CONFLICT,
            )
        source_rows = repository.db_reconciliation_sources(
            settings.account_id, report_date_from, report_date_to, instrument_id
        )
        diffs = analytics_build_reconciliation_diffs(source_rows)
        if output == "csv":
            return _csv_response(
                "reconciliation-diff-v1.csv", _RECONCILIATION_COLUMNS, [_reconciliation_csv(row) for row in diffs]
            )
        return JSONResponse(
            content={"schema_version": "v1", "tolerance_policy": "MVP_spec_freeze.md#4",
                     "items": [_reconciliation_json(row) for row in diffs],
                     "filters": _filters(report_date_from, report_date_to, instrument_id)}
        )

    return router


def _validate_report_query(date_from: date | None, date_to: date | None, output: str) -> JSONResponse | None:
    if date_from is not None and date_to is not None and date_from > date_to:
        return JSONResponse(
            content={"status": "error", "code": "INVALID_DATE_RANGE", "message": "from date exceeds to date"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if output not in {"json", "csv"}:
        return JSONResponse(
            content={"status": "error", "code": "INVALID_FORMAT", "message": "format must be json or csv"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _csv_response(filename: str, columns: tuple[str, ...], rows: list[dict[str, object]]) -> Response:
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=stream.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Schema-Version": "v1"},
    )


def _instrument_json(row: InstrumentPnlReportRecord) -> dict[str, object]:
    payload = _instrument_csv(row)
    position = Decimal(row.position_qty)
    if position == 0 or row.cost_basis is None:
        payload.update({"average_cost": None, "total_cost": None, "last_day_value": None})
    else:
        total_cost = Decimal(row.cost_basis)
        payload.update({
            "average_cost": str(total_cost / position),
            "total_cost": str(total_cost),
            "last_day_value": str(total_cost + Decimal(row.unrealized_pnl)),
        })
    payload["provisional"] = row.provisional
    payload["unresolved_case_count"] = row.unresolved_case_count
    return payload


def _instrument_csv(row: InstrumentPnlReportRecord) -> dict[str, object]:
    return {
        "report_date_local": row.report_date_local.isoformat(), "instrument_id": str(row.instrument_id),
        "conid": row.conid, "symbol": row.symbol, "currency": row.currency, "position_qty": row.position_qty,
        "cost_basis": row.cost_basis, "realized_pnl": row.realized_pnl, "unrealized_pnl": row.unrealized_pnl,
        "total_pnl": row.total_pnl, "provisional": _boolean(row.provisional),
    }


def _label_json(row: LabelPnlReportRecord) -> dict[str, object]:
    payload = _label_csv(row)
    payload["provisional"] = row.provisional
    return payload


def _label_csv(row: LabelPnlReportRecord) -> dict[str, object]:
    return {
        "report_date_local": row.report_date_local.isoformat(), "label_id": str(row.label_id),
        "label_name": row.label_name, "instrument_count": row.instrument_count, "realized_pnl": row.realized_pnl,
        "unrealized_pnl": row.unrealized_pnl, "total_pnl": row.total_pnl, "fees": row.fees,
        "withholding_tax": row.withholding_tax, "provisional": _boolean(row.provisional),
    }


def _reconciliation_json(row: ReconciliationDiff) -> dict[str, object]:
    payload = _reconciliation_csv(row)
    payload["within_tolerance"] = row.within_tolerance
    payload["provisional"] = row.provisional
    return payload


def _reconciliation_csv(row: ReconciliationDiff) -> dict[str, object]:
    values = asdict(row)
    values["report_date_local"] = row.report_date_local.isoformat()
    for key in ("instrument_id", "source_event_id", "source_raw_record_id"):
        value = values[key]
        values[key] = "" if value is None else str(value)
    for key in ("broker_value", "economic_value", "abs_diff", "rel_diff", "tolerance_abs", "tolerance_rel"):
        value = values[key]
        values[key] = "" if value is None else str(value)
    values["within_tolerance"] = _boolean(row.within_tolerance)
    values["provisional"] = _boolean(row.provisional)
    return values


def _filters(date_from: date | None, date_to: date | None, identifier: UUID | None) -> dict[str, object]:
    return {"report_date_from": None if date_from is None else date_from.isoformat(),
            "report_date_to": None if date_to is None else date_to.isoformat(),
            "id": None if identifier is None else str(identifier)}


def _boolean(value: bool) -> str:
    return "true" if value else "false"
