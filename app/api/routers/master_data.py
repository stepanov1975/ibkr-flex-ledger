"""Instrument, label, and note API workflows."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import AppSettings
from app.db import InstrumentRecord, LabelRecord, NoteRecord, PortfolioRepositoryPort


class LabelCreatePayload(BaseModel):
    name: str
    color: str | None = None


class LabelUpdatePayload(BaseModel):
    name: str | None = None
    color: str | None = None


class NoteCreatePayload(BaseModel):
    instrument_id: UUID | None = None
    label_id: UUID | None = None
    content: str


class NoteUpdatePayload(BaseModel):
    content: str


def api_create_master_data_router(settings: AppSettings, repository: PortfolioRepositoryPort) -> APIRouter:
    """Create master-data CRUD endpoints."""

    router = APIRouter(tags=["portfolio"])

    @router.get("/instruments")
    def instrument_list(
        limit: int = Query(default=settings.api_default_limit),
        offset: int = Query(default=0),
        sort_by: str = Query(default="symbol"),
        sort_dir: str = Query(default="asc"),
        label_id: UUID | None = Query(default=None),
        search: str | None = Query(default=None),
        active_only: bool = Query(default=True),
    ) -> JSONResponse:
        validation = _validate_list(limit, offset, sort_by, sort_dir, {"symbol", "conid", "updated_at_utc"})
        if validation is not None:
            return validation
        applied_limit = min(limit, settings.api_max_limit)
        rows, total = repository.db_instrument_list(
            settings.account_id, applied_limit, offset, sort_by, sort_dir, label_id, search, active_only
        )
        return JSONResponse(
            content={
                "items": [_instrument(row) for row in rows],
                "page": _page(limit, applied_limit, offset, len(rows), total),
                "sort": {"sort_by": sort_by, "sort_dir": sort_dir},
                "filters": {"label_id": _uuid(label_id), "search": search, "active_only": active_only},
            }
        )

    @router.get("/instruments/{instrument_id}")
    def instrument_detail(instrument_id: UUID) -> JSONResponse:
        row = repository.db_instrument_get(settings.account_id, instrument_id)
        if row is None:
            return _error(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "instrument not found")
        return JSONResponse(content=_instrument(row))

    @router.get("/labels")
    def label_list() -> JSONResponse:
        return JSONResponse(content={"items": [_label(row) for row in repository.db_label_list()]})

    @router.post("/labels", status_code=status.HTTP_201_CREATED)
    def label_create(payload: LabelCreatePayload) -> JSONResponse:
        try:
            row = repository.db_label_create(payload.name, payload.color)
        except ValueError as error:
            return _error(status.HTTP_409_CONFLICT, "LABEL_CONFLICT", str(error))
        return JSONResponse(content=_label(row), status_code=status.HTTP_201_CREATED)

    @router.patch("/labels/{label_id}")
    def label_update(label_id: UUID, payload: LabelUpdatePayload) -> JSONResponse:
        try:
            row = repository.db_label_update(
                label_id, payload.name, payload.color, update_color="color" in payload.model_fields_set
            )
        except ValueError as error:
            return _error(status.HTTP_409_CONFLICT, "LABEL_CONFLICT", str(error))
        if row is None:
            return _error(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "label not found")
        return JSONResponse(content=_label(row))

    @router.delete("/labels/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
    def label_delete(label_id: UUID) -> JSONResponse:
        try:
            deleted = repository.db_label_delete(label_id)
        except ValueError as error:
            return _error(status.HTTP_409_CONFLICT, "LABEL_IN_USE", str(error))
        if not deleted:
            return _error(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "label not found")
        return JSONResponse(content=None, status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/instruments/{instrument_id}/labels/{label_id}")
    def instrument_label_assign(instrument_id: UUID, label_id: UUID) -> JSONResponse:
        try:
            created = repository.db_instrument_label_assign(instrument_id, label_id)
        except LookupError:
            return _error(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "instrument or label not found")
        return JSONResponse(content={"assigned": True, "created": created})

    @router.delete("/instruments/{instrument_id}/labels/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
    def instrument_label_remove(instrument_id: UUID, label_id: UUID) -> JSONResponse:
        if not repository.db_instrument_label_remove(instrument_id, label_id):
            return _error(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "assignment not found")
        return JSONResponse(content=None, status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/notes", status_code=status.HTTP_201_CREATED)
    def note_create(payload: NoteCreatePayload) -> JSONResponse:
        try:
            row = repository.db_note_create(payload.instrument_id, payload.label_id, payload.content)
        except ValueError as error:
            return _error(status.HTTP_400_BAD_REQUEST, "INVALID_NOTE", str(error))
        except LookupError as error:
            return _error(status.HTTP_404_NOT_FOUND, "NOT_FOUND", str(error))
        return JSONResponse(content=_note(row), status_code=status.HTTP_201_CREATED)

    @router.get("/notes")
    def note_list(
        limit: int = Query(default=settings.api_default_limit),
        offset: int = Query(default=0),
        sort_by: str = Query(default="created_at_utc"),
        sort_dir: str = Query(default="desc"),
        instrument_id: UUID | None = Query(default=None),
        label_id: UUID | None = Query(default=None),
        created_from: datetime | None = Query(default=None),
        created_to: datetime | None = Query(default=None),
    ) -> JSONResponse:
        validation = _validate_list(
            limit, offset, sort_by, sort_dir, {"created_at_utc", "updated_at_utc", "instrument_id"}
        )
        if validation is not None:
            return validation
        applied_limit = min(limit, settings.api_max_limit)
        rows, total = repository.db_note_list(
            applied_limit, offset, sort_by, sort_dir, instrument_id, label_id, created_from, created_to
        )
        return JSONResponse(
            content={
                "items": [_note(row) for row in rows],
                "page": _page(limit, applied_limit, offset, len(rows), total),
                "sort": {"sort_by": sort_by, "sort_dir": sort_dir},
                "filters": {
                    "instrument_id": _uuid(instrument_id),
                    "label_id": _uuid(label_id),
                    "created_from": _datetime(created_from),
                    "created_to": _datetime(created_to),
                },
            }
        )

    @router.patch("/notes/{note_id}")
    def note_update(note_id: UUID, payload: NoteUpdatePayload) -> JSONResponse:
        try:
            row = repository.db_note_update(note_id, payload.content)
        except ValueError as error:
            return _error(status.HTTP_400_BAD_REQUEST, "INVALID_NOTE", str(error))
        if row is None:
            return _error(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "note not found")
        return JSONResponse(content=_note(row))

    @router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
    def note_delete(note_id: UUID) -> JSONResponse:
        if not repository.db_note_delete(note_id):
            return _error(status.HTTP_404_NOT_FOUND, "NOT_FOUND", "note not found")
        return JSONResponse(content=None, status_code=status.HTTP_204_NO_CONTENT)

    return router


def _validate_list(limit: int, offset: int, sort_by: str, sort_dir: str, allowed: set[str]) -> JSONResponse | None:
    if limit < 1 or offset < 0:
        return _error(status.HTTP_400_BAD_REQUEST, "INVALID_PAGINATION", "limit must be >= 1 and offset >= 0")
    if sort_by not in allowed:
        return _error(status.HTTP_400_BAD_REQUEST, "INVALID_SORT_FIELD", f"unsupported sort_by={sort_by}")
    if sort_dir not in {"asc", "desc"}:
        return _error(status.HTTP_400_BAD_REQUEST, "INVALID_SORT_DIRECTION", f"unsupported sort_dir={sort_dir}")
    return None


def _page(limit: int, applied: int, offset: int, returned: int, total: int) -> dict[str, object]:
    return {
        "limit": limit,
        "applied_limit": applied,
        "offset": offset,
        "returned": returned,
        "total": total,
        "has_more": offset + returned < total,
    }


def _instrument(row: InstrumentRecord) -> dict[str, object]:
    return {
        "instrument_id": str(row.instrument_id), "conid": row.conid, "symbol": row.symbol,
        "currency": row.currency, "asset_category": row.asset_category, "description": row.description,
        "active": row.active, "labels": list(row.labels), "updated_at_utc": row.updated_at_utc.isoformat(),
    }


def _label(row: LabelRecord) -> dict[str, object]:
    return {"label_id": str(row.label_id), "name": row.name, "color": row.color,
        "created_at_utc": row.created_at_utc.isoformat(), "updated_at_utc": row.updated_at_utc.isoformat()}


def _note(row: NoteRecord) -> dict[str, object]:
    return {"note_id": str(row.note_id), "instrument_id": _uuid(row.instrument_id), "label_id": _uuid(row.label_id),
        "content": row.content, "created_at_utc": row.created_at_utc.isoformat(),
        "updated_at_utc": row.updated_at_utc.isoformat()}


def _error(code: int, error_code: str, message: str) -> JSONResponse:
    return JSONResponse(content={"status": "error", "code": error_code, "message": message}, status_code=code)


def _uuid(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _datetime(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
