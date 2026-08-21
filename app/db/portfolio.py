"""PostgreSQL persistence for portfolio workflows, reports, and operations."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .portfolio_interfaces import (
    CorporateActionManualCaseRecord,
    DiagnosticArchiveRecord,
    IngestionSloRecord,
    InstrumentPnlReportRecord,
    InstrumentRecord,
    LabelPnlReportRecord,
    LabelRecord,
    NoteRecord,
    ProvenanceRecord,
    ReconciliationSourceRecord,
)


class SQLAlchemyPortfolioService:
    """Database implementation for the remaining MVP portfolio surfaces."""

    _INSTRUMENT_SORTS = {
        ("symbol", "asc"): "i.symbol asc, i.instrument_id asc",
        ("symbol", "desc"): "i.symbol desc, i.instrument_id desc",
        ("conid", "asc"): "i.conid asc, i.instrument_id asc",
        ("conid", "desc"): "i.conid desc, i.instrument_id desc",
        ("updated_at_utc", "asc"): "i.updated_at_utc asc, i.instrument_id asc",
        ("updated_at_utc", "desc"): "i.updated_at_utc desc, i.instrument_id desc",
    }
    _NOTE_SORTS = {
        ("created_at_utc", "asc"): "n.created_at_utc asc, n.note_id asc",
        ("created_at_utc", "desc"): "n.created_at_utc desc, n.note_id desc",
        ("updated_at_utc", "asc"): "n.updated_at_utc asc, n.note_id asc",
        ("updated_at_utc", "desc"): "n.updated_at_utc desc, n.note_id desc",
        ("instrument_id", "asc"): "n.instrument_id asc nulls last, n.note_id asc",
        ("instrument_id", "desc"): "n.instrument_id desc nulls last, n.note_id desc",
    }

    def __init__(self, engine: Engine):
        if engine is None:
            raise ValueError("engine must not be None")
        self._engine = engine

    def db_instrument_list(
        self,
        account_id: str,
        limit: int,
        offset: int,
        sort_by: str,
        sort_dir: str,
        label_id: UUID | None,
        search: str | None,
        active_only: bool,
    ) -> tuple[list[InstrumentRecord], int]:
        order_by = self._INSTRUMENT_SORTS.get((sort_by, sort_dir))
        if order_by is None:
            raise ValueError("unsupported instrument sort")
        params = {
            "account_id": self._text(account_id, "account_id"),
            "limit": limit,
            "offset": offset,
            "label_id": label_id,
            "search": None if search is None or not search.strip() else f"%{search.strip()}%",
            "active_only": active_only,
        }
        where = (
            "i.account_id = :account_id "
            "AND (:active_only = false OR i.active = true) "
            "AND (CAST(:search AS text) IS NULL OR i.symbol ILIKE :search OR i.conid ILIKE :search "
            "OR COALESCE(i.description, '') ILIKE :search) "
            "AND (CAST(:label_id AS uuid) IS NULL OR EXISTS (SELECT 1 FROM instrument_label filter_label "
            "WHERE filter_label.instrument_id = i.instrument_id AND filter_label.label_id = CAST(:label_id AS uuid)))"
        )
        select = (
            "SELECT i.instrument_id, i.conid, i.symbol, i.currency, i.asset_category, i.description, i.active, "
            "i.updated_at_utc, COALESCE(jsonb_agg(jsonb_build_object('label_id', l.label_id::text, "
            "'name', l.name, 'color', l.color) ORDER BY l.name) FILTER (WHERE l.label_id IS NOT NULL), '[]') AS labels "
            "FROM instrument i LEFT JOIN instrument_label il ON il.instrument_id = i.instrument_id "
            "LEFT JOIN label l ON l.label_id = il.label_id WHERE " + where + " GROUP BY i.instrument_id ORDER BY " + order_by
        )
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(text(select + " LIMIT :limit OFFSET :offset"), params).mappings().all()
                total = int(
                    connection.execute(text("SELECT count(*) FROM instrument i WHERE " + where), params).scalar_one()
                )
        except SQLAlchemyError as error:
            raise RuntimeError("instrument list failed") from error
        return ([self._instrument(row) for row in rows], total)

    def db_instrument_get(self, account_id: str, instrument_id: UUID) -> InstrumentRecord | None:
        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT i.instrument_id, i.conid, i.symbol, i.currency, i.asset_category, i.description, i.active, "
                        "i.updated_at_utc, COALESCE(jsonb_agg(jsonb_build_object('label_id', l.label_id::text, "
                        "'name', l.name, 'color', l.color) ORDER BY l.name) FILTER (WHERE l.label_id IS NOT NULL), '[]') AS labels "
                        "FROM instrument i LEFT JOIN instrument_label il ON il.instrument_id=i.instrument_id "
                        "LEFT JOIN label l ON l.label_id=il.label_id WHERE i.account_id=:account_id "
                        "AND i.instrument_id=:instrument_id GROUP BY i.instrument_id"
                    ),
                    {"account_id": self._text(account_id, "account_id"), "instrument_id": instrument_id},
                ).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise RuntimeError("instrument detail failed") from error
        return None if row is None else self._instrument(row)

    def db_label_list(self) -> list[LabelRecord]:
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(text("SELECT * FROM label ORDER BY name asc, label_id asc")).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("label list failed") from error
        return [self._label(row) for row in rows]

    def db_label_create(self, name: str, color: str | None) -> LabelRecord:
        try:
            with self._engine.begin() as connection:
                row = connection.execute(
                    text("INSERT INTO label (name, color) VALUES (:name, :color) RETURNING *"),
                    {"name": self._text(name, "name"), "color": self._optional_text(color)},
                ).mappings().one()
        except IntegrityError as error:
            raise ValueError("label name already exists") from error
        except SQLAlchemyError as error:
            raise RuntimeError("label create failed") from error
        return self._label(row)

    def db_label_update(self, label_id: UUID, name: str | None, color: str | None) -> LabelRecord | None:
        try:
            with self._engine.begin() as connection:
                row = connection.execute(
                    text(
                        "UPDATE label SET name=COALESCE(:name, name), color=COALESCE(:color, color), "
                        "updated_at_utc=now() WHERE label_id=:label_id RETURNING *"
                    ),
                    {"label_id": label_id, "name": self._optional_text(name), "color": self._optional_text(color)},
                ).mappings().one_or_none()
        except IntegrityError as error:
            raise ValueError("label name already exists") from error
        except SQLAlchemyError as error:
            raise RuntimeError("label update failed") from error
        return None if row is None else self._label(row)

    def db_label_delete(self, label_id: UUID) -> bool:
        return self._delete("DELETE FROM label WHERE label_id=:id", label_id)

    def db_instrument_label_assign(self, instrument_id: UUID, label_id: UUID) -> bool:
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    text(
                        "INSERT INTO instrument_label (instrument_id, label_id) VALUES (:instrument_id, :label_id) "
                        "ON CONFLICT ON CONSTRAINT uq_instrument_label_pair DO NOTHING"
                    ),
                    {"instrument_id": instrument_id, "label_id": label_id},
                )
        except IntegrityError as error:
            raise LookupError("instrument or label not found") from error
        except SQLAlchemyError as error:
            raise RuntimeError("instrument label assignment failed") from error
        return bool(result.rowcount)

    def db_instrument_label_remove(self, instrument_id: UUID, label_id: UUID) -> bool:
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    text("DELETE FROM instrument_label WHERE instrument_id=:instrument_id AND label_id=:label_id"),
                    {"instrument_id": instrument_id, "label_id": label_id},
                )
        except SQLAlchemyError as error:
            raise RuntimeError("instrument label removal failed") from error
        return bool(result.rowcount)

    def db_note_create(self, instrument_id: UUID | None, label_id: UUID | None, content: str) -> NoteRecord:
        if instrument_id is None and label_id is None:
            raise ValueError("instrument_id or label_id is required")
        try:
            with self._engine.begin() as connection:
                row = connection.execute(
                    text(
                        "INSERT INTO note (instrument_id, label_id, content) "
                        "VALUES (:instrument_id, :label_id, :content) RETURNING *"
                    ),
                    {"instrument_id": instrument_id, "label_id": label_id, "content": self._text(content, "content")},
                ).mappings().one()
        except IntegrityError as error:
            raise LookupError("instrument or label not found") from error
        except SQLAlchemyError as error:
            raise RuntimeError("note create failed") from error
        return self._note(row)

    def db_note_update(self, note_id: UUID, content: str) -> NoteRecord | None:
        try:
            with self._engine.begin() as connection:
                row = connection.execute(
                    text("UPDATE note SET content=:content, updated_at_utc=now() WHERE note_id=:id RETURNING *"),
                    {"id": note_id, "content": self._text(content, "content")},
                ).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise RuntimeError("note update failed") from error
        return None if row is None else self._note(row)

    def db_note_delete(self, note_id: UUID) -> bool:
        return self._delete("DELETE FROM note WHERE note_id=:id", note_id)

    def db_note_list(
        self,
        limit: int,
        offset: int,
        sort_by: str,
        sort_dir: str,
        instrument_id: UUID | None,
        label_id: UUID | None,
        created_from: datetime | None,
        created_to: datetime | None,
    ) -> tuple[list[NoteRecord], int]:
        order_by = self._NOTE_SORTS.get((sort_by, sort_dir))
        if order_by is None:
            raise ValueError("unsupported note sort")
        where = (
            "(CAST(:instrument_id AS uuid) IS NULL OR n.instrument_id=CAST(:instrument_id AS uuid)) "
            "AND (CAST(:label_id AS uuid) IS NULL OR n.label_id=CAST(:label_id AS uuid)) "
            "AND (CAST(:created_from AS timestamptz) IS NULL OR n.created_at_utc>=CAST(:created_from AS timestamptz)) "
            "AND (CAST(:created_to AS timestamptz) IS NULL OR n.created_at_utc<=CAST(:created_to AS timestamptz))"
        )
        params = {
            "instrument_id": instrument_id,
            "label_id": label_id,
            "created_from": created_from,
            "created_to": created_to,
            "limit": limit,
            "offset": offset,
        }
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text("SELECT n.* FROM note n WHERE " + where + " ORDER BY " + order_by + " LIMIT :limit OFFSET :offset"),
                    params,
                ).mappings().all()
                total = int(connection.execute(text("SELECT count(*) FROM note n WHERE " + where), params).scalar_one())
        except SQLAlchemyError as error:
            raise RuntimeError("note list failed") from error
        return ([self._note(row) for row in rows], total)

    def db_manual_case_list(self, status: str | None) -> list[CorporateActionManualCaseRecord]:
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT c.*, i.symbol FROM corporate_action_manual_case c JOIN instrument i USING (instrument_id) "
                        "WHERE (CAST(:status AS text) IS NULL OR c.status=:status) "
                        "ORDER BY CASE c.status WHEN 'open' THEN 0 ELSE 1 END, c.created_at_utc desc, c.case_id desc"
                    ),
                    {"status": self._optional_text(status)},
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("manual case list failed") from error
        return [self._manual_case(row) for row in rows]

    def db_manual_case_update(
        self,
        case_id: UUID,
        status: str,
        owner: str | None,
        resolution_note: str | None,
    ) -> CorporateActionManualCaseRecord | None:
        if status not in {"open", "resolved", "dismissed"}:
            raise ValueError("invalid manual case status")
        if status != "open" and not self._optional_text(resolution_note):
            raise ValueError("resolution_note is required when closing a case")
        try:
            with self._engine.begin() as connection:
                row = connection.execute(
                    text(
                        "UPDATE corporate_action_manual_case SET status=:status, owner=:owner, "
                        "resolution_note=:resolution_note, resolved_at_utc=CASE WHEN :status='open' THEN NULL ELSE now() END, "
                        "updated_at_utc=now() WHERE case_id=:case_id RETURNING *"
                    ),
                    {
                        "case_id": case_id,
                        "status": status,
                        "owner": self._optional_text(owner),
                        "resolution_note": self._optional_text(resolution_note),
                    },
                ).mappings().one_or_none()
                if row is None:
                    return None
                instrument_id = row["instrument_id"]
                connection.execute(
                    text(
                        "UPDATE event_corp_action SET provisional=EXISTS (SELECT 1 FROM corporate_action_manual_case c "
                        "WHERE c.event_corp_action_id=event_corp_action.event_corp_action_id AND c.status='open') "
                        "WHERE instrument_id=:instrument_id"
                    ),
                    {"instrument_id": instrument_id},
                )
                connection.execute(
                    text(
                        "UPDATE pnl_snapshot_daily SET provisional=EXISTS (SELECT 1 FROM corporate_action_manual_case c "
                        "WHERE c.instrument_id=:instrument_id AND c.status='open') WHERE instrument_id=:instrument_id"
                    ),
                    {"instrument_id": instrument_id},
                )
                full_row = connection.execute(
                    text(
                        "SELECT c.*, i.symbol FROM corporate_action_manual_case c JOIN instrument i USING (instrument_id) "
                        "WHERE c.case_id=:case_id"
                    ),
                    {"case_id": case_id},
                ).mappings().one()
        except SQLAlchemyError as error:
            raise RuntimeError("manual case update failed") from error
        return self._manual_case(full_row)

    def db_report_pnl_by_instrument(
        self,
        account_id: str,
        report_date_from: date | None,
        report_date_to: date | None,
        instrument_id: UUID | None,
    ) -> list[InstrumentPnlReportRecord]:
        query = text(
            "SELECT s.report_date_local, s.instrument_id, i.conid, i.symbol, s.currency, s.position_qty, s.cost_basis, "
            "s.realized_pnl, s.unrealized_pnl, s.total_pnl, s.provisional, "
            "(SELECT count(*) FROM corporate_action_manual_case c WHERE c.instrument_id=s.instrument_id AND c.status='open') "
            "AS unresolved_case_count FROM pnl_snapshot_daily s JOIN instrument i USING (instrument_id) "
            "WHERE s.account_id=:account_id AND (CAST(:date_from AS date) IS NULL OR s.report_date_local>=:date_from) "
            "AND (CAST(:date_to AS date) IS NULL OR s.report_date_local<=:date_to) "
            "AND (CAST(:instrument_id AS uuid) IS NULL OR s.instrument_id=CAST(:instrument_id AS uuid)) "
            "ORDER BY s.report_date_local asc, i.symbol asc, s.instrument_id asc"
        )
        rows = self._report_rows(query, account_id, report_date_from, report_date_to, instrument_id)
        return [
            InstrumentPnlReportRecord(
                report_date_local=row["report_date_local"], instrument_id=row["instrument_id"], conid=row["conid"],
                symbol=row["symbol"], currency=row["currency"], position_qty=str(row["position_qty"]),
                cost_basis=None if row["cost_basis"] is None else str(row["cost_basis"]),
                realized_pnl=str(row["realized_pnl"]), unrealized_pnl=str(row["unrealized_pnl"]),
                total_pnl=str(row["total_pnl"]), provisional=bool(row["provisional"]),
                unresolved_case_count=int(row["unresolved_case_count"]),
            )
            for row in rows
        ]

    def db_report_pnl_by_label(
        self,
        account_id: str,
        report_date_from: date | None,
        report_date_to: date | None,
        label_id: UUID | None,
    ) -> list[LabelPnlReportRecord]:
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT s.report_date_local, l.label_id, l.name AS label_name, count(DISTINCT s.instrument_id) "
                        "AS instrument_count, sum(s.realized_pnl) AS realized_pnl, sum(s.unrealized_pnl) AS unrealized_pnl, "
                        "sum(s.total_pnl) AS total_pnl, sum(s.fees) AS fees, sum(s.withholding_tax) AS withholding_tax, "
                        "bool_or(s.provisional) AS provisional FROM pnl_snapshot_daily s "
                        "JOIN instrument_label il USING (instrument_id) JOIN label l USING (label_id) "
                        "WHERE s.account_id=:account_id AND (CAST(:date_from AS date) IS NULL OR s.report_date_local>=:date_from) "
                        "AND (CAST(:date_to AS date) IS NULL OR s.report_date_local<=:date_to) "
                        "AND (CAST(:label_id AS uuid) IS NULL OR l.label_id=CAST(:label_id AS uuid)) "
                        "GROUP BY s.report_date_local, l.label_id, l.name ORDER BY s.report_date_local asc, l.name asc, l.label_id asc"
                    ),
                    {"account_id": self._text(account_id, "account_id"), "date_from": report_date_from,
                     "date_to": report_date_to, "label_id": label_id},
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("label report failed") from error
        return [
            LabelPnlReportRecord(
                report_date_local=row["report_date_local"], label_id=row["label_id"], label_name=row["label_name"],
                instrument_count=int(row["instrument_count"]), realized_pnl=str(row["realized_pnl"]),
                unrealized_pnl=str(row["unrealized_pnl"]), total_pnl=str(row["total_pnl"]), fees=str(row["fees"]),
                withholding_tax=str(row["withholding_tax"]), provisional=bool(row["provisional"]),
            )
            for row in rows
        ]

    def db_report_provenance(
        self,
        account_id: str,
        report_date_local: date,
        instrument_id: UUID,
    ) -> list[ProvenanceRecord]:
        unions = []
        for table, id_column, event_type in (
            ("event_trade_fill", "event_trade_fill_id", "trade_fill"),
            ("event_cashflow", "event_cashflow_id", "cashflow"),
            ("event_corp_action", "event_corp_action_id", "corp_action"),
        ):
            unions.append(
                f"SELECT '{event_type}' AS event_type, event.{id_column} AS event_id, event.source_raw_record_id, "
                "raw.section_name, raw.source_row_ref, raw.source_payload FROM " + table + " event "
                "JOIN raw_record raw ON raw.raw_record_id=event.source_raw_record_id WHERE event.account_id=:account_id "
                "AND event.report_date_local=:report_date AND event.instrument_id=:instrument_id"
            )
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(" UNION ALL ".join(unions) + " ORDER BY event_type, event_id"),
                    {"account_id": self._text(account_id, "account_id"), "report_date": report_date_local,
                     "instrument_id": instrument_id},
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("provenance report failed") from error
        return [ProvenanceRecord(**dict(row)) for row in rows]

    def db_reconciliation_sources(
        self,
        account_id: str,
        report_date_from: date | None,
        report_date_to: date | None,
        instrument_id: UUID | None,
    ) -> list[ReconciliationSourceRecord]:
        query = text(
            "SELECT s.report_date_local, s.instrument_id, i.conid, i.symbol, s.currency, s.position_qty, "
            "s.realized_pnl, s.unrealized_pnl, s.fees, s.withholding_tax, s.provisional, "
            "(SELECT NULLIF(raw.source_payload->>'position','')::numeric FROM raw_record raw WHERE raw.account_id=s.account_id "
            "AND raw.section_name='OpenPositions' AND raw.report_date_local=s.report_date_local "
            "AND raw.source_payload->>'conid'=i.conid ORDER BY raw.raw_record_id DESC LIMIT 1) AS broker_position_qty, "
            "(SELECT sum(NULLIF(raw.source_payload->>'fifoPnlRealized','')::numeric) FROM raw_record raw "
            "WHERE raw.account_id=s.account_id AND raw.section_name='Trades' AND raw.report_date_local=s.report_date_local "
            "AND raw.source_payload->>'conid'=i.conid) AS broker_realized_pnl, "
            "(SELECT NULLIF(raw.source_payload->>'fifoPnlUnrealized','')::numeric FROM raw_record raw "
            "WHERE raw.account_id=s.account_id AND raw.section_name='OpenPositions' AND raw.report_date_local=s.report_date_local "
            "AND raw.source_payload->>'conid'=i.conid ORDER BY raw.raw_record_id DESC LIMIT 1) AS broker_unrealized_pnl, "
            "(SELECT sum(COALESCE(NULLIF(raw.source_payload->>'commission','')::numeric,0) + "
            "COALESCE(NULLIF(raw.source_payload->>'fees','')::numeric,0)) FROM raw_record raw WHERE raw.account_id=s.account_id "
            "AND raw.section_name='Trades' AND raw.report_date_local=s.report_date_local "
            "AND raw.source_payload->>'conid'=i.conid) AS broker_fees, "
            "(SELECT sum(NULLIF(raw.source_payload->>'withholdingTax','')::numeric) FROM raw_record raw "
            "WHERE raw.account_id=s.account_id AND raw.section_name='CashTransactions' "
            "AND raw.report_date_local=s.report_date_local AND raw.source_payload->>'conid'=i.conid) AS broker_withholding_tax, "
            "(SELECT event_trade_fill_id FROM event_trade_fill event WHERE event.instrument_id=s.instrument_id "
            "AND event.report_date_local=s.report_date_local ORDER BY event_trade_fill_id LIMIT 1) AS source_event_id, "
            "(SELECT source_raw_record_id FROM event_trade_fill event WHERE event.instrument_id=s.instrument_id "
            "AND event.report_date_local=s.report_date_local ORDER BY event_trade_fill_id LIMIT 1) AS source_raw_record_id "
            "FROM pnl_snapshot_daily s JOIN instrument i USING (instrument_id) WHERE s.account_id=:account_id "
            "AND (CAST(:date_from AS date) IS NULL OR s.report_date_local>=:date_from) "
            "AND (CAST(:date_to AS date) IS NULL OR s.report_date_local<=:date_to) "
            "AND (CAST(:instrument_id AS uuid) IS NULL OR s.instrument_id=CAST(:instrument_id AS uuid)) "
            "ORDER BY s.report_date_local asc, i.symbol asc, s.instrument_id asc"
        )
        rows = self._report_rows(query, account_id, report_date_from, report_date_to, instrument_id)
        return [
            ReconciliationSourceRecord(
                report_date_local=row["report_date_local"], instrument_id=row["instrument_id"], conid=row["conid"],
                symbol=row["symbol"], currency=row["currency"], position_qty=str(row["position_qty"]),
                realized_pnl=str(row["realized_pnl"]), unrealized_pnl=str(row["unrealized_pnl"]), fees=str(row["fees"]),
                withholding_tax=str(row["withholding_tax"]),
                broker_position_qty=self._numeric(row["broker_position_qty"]),
                broker_realized_pnl=self._numeric(row["broker_realized_pnl"]),
                broker_unrealized_pnl=self._numeric(row["broker_unrealized_pnl"]),
                broker_fees=self._numeric(row["broker_fees"]),
                broker_withholding_tax=self._numeric(row["broker_withholding_tax"]),
                source_event_id=row["source_event_id"], source_raw_record_id=row["source_raw_record_id"],
                provisional=bool(row["provisional"]),
            )
            for row in rows
        ]

    def db_reconciliation_missing_sections(
        self,
        account_id: str,
        report_date_from: date | None,
        report_date_to: date | None,
    ) -> list[str]:
        required = {"MTMPerformanceSummaryInBase", "FIFOPerformanceSummaryInBase"}
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT DISTINCT section_name FROM raw_record WHERE account_id=:account_id "
                        "AND (CAST(:date_from AS date) IS NULL OR report_date_local>=:date_from) "
                        "AND (CAST(:date_to AS date) IS NULL OR report_date_local<=:date_to) "
                        "AND section_name = ANY(:required)"
                    ),
                    {"account_id": self._text(account_id, "account_id"), "date_from": report_date_from,
                     "date_to": report_date_to, "required": sorted(required)},
                ).scalars().all()
        except SQLAlchemyError as error:
            raise RuntimeError("reconciliation section check failed") from error
        return sorted(required.difference(rows))

    def db_ingestion_slo_records(self, account_id: str, since_utc: datetime) -> list[IngestionSloRecord]:
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT status, started_at_utc, ended_at_utc, duration_ms FROM ingestion_run "
                        "WHERE account_id=:account_id AND started_at_utc>=:since_utc ORDER BY started_at_utc asc"
                    ),
                    {"account_id": self._text(account_id, "account_id"), "since_utc": since_utc},
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("SLO records read failed") from error
        return [IngestionSloRecord(**dict(row)) for row in rows]

    def db_diagnostics_archive_candidates(self, cutoff_utc: datetime) -> list[DiagnosticArchiveRecord]:
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT ingestion_run_id, account_id, run_type, status, started_at_utc, diagnostics "
                        "FROM ingestion_run WHERE diagnostics IS NOT NULL AND started_at_utc<:cutoff "
                        "ORDER BY started_at_utc asc, ingestion_run_id asc"
                    ),
                    {"cutoff": cutoff_utc},
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("diagnostic archive candidate read failed") from error
        return [DiagnosticArchiveRecord(**dict(row)) for row in rows]

    def db_diagnostics_purge(self, ingestion_run_ids: list[UUID]) -> int:
        if not ingestion_run_ids:
            return 0
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    text("UPDATE ingestion_run SET diagnostics=NULL WHERE ingestion_run_id = ANY(:run_ids)"),
                    {"run_ids": ingestion_run_ids},
                )
        except SQLAlchemyError as error:
            raise RuntimeError("diagnostic purge failed") from error
        return int(result.rowcount or 0)

    def _report_rows(
        self, query: Any, account_id: str, date_from: date | None, date_to: date | None, instrument_id: UUID | None
    ) -> list[Any]:
        try:
            with self._engine.connect() as connection:
                return list(connection.execute(query, {"account_id": self._text(account_id, "account_id"),
                    "date_from": date_from, "date_to": date_to, "instrument_id": instrument_id}).mappings().all())
        except SQLAlchemyError as error:
            raise RuntimeError("report read failed") from error

    def _delete(self, statement: str, identifier: UUID) -> bool:
        try:
            with self._engine.begin() as connection:
                result = connection.execute(text(statement), {"id": identifier})
        except SQLAlchemyError as error:
            raise RuntimeError("delete failed") from error
        return bool(result.rowcount)

    @staticmethod
    def _instrument(row: Any) -> InstrumentRecord:
        labels = tuple(dict(item) for item in (row["labels"] or []))
        return InstrumentRecord(
            instrument_id=row["instrument_id"], conid=row["conid"], symbol=row["symbol"], currency=row["currency"],
            asset_category=row["asset_category"], description=row["description"], active=bool(row["active"]),
            labels=labels, updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _label(row: Any) -> LabelRecord:
        return LabelRecord(label_id=row["label_id"], name=row["name"], color=row["color"],
            created_at_utc=row["created_at_utc"], updated_at_utc=row["updated_at_utc"])

    @staticmethod
    def _note(row: Any) -> NoteRecord:
        return NoteRecord(note_id=row["note_id"], instrument_id=row["instrument_id"], label_id=row["label_id"],
            content=row["content"], created_at_utc=row["created_at_utc"], updated_at_utc=row["updated_at_utc"])

    @staticmethod
    def _manual_case(row: Any) -> CorporateActionManualCaseRecord:
        return CorporateActionManualCaseRecord(
            case_id=row["case_id"], event_corp_action_id=row["event_corp_action_id"], action_type=row["action_type"],
            instrument_id=row["instrument_id"], symbol=row["symbol"], status=row["status"], owner=row["owner"],
            resolution_note=row["resolution_note"], resolved_at_utc=row["resolved_at_utc"],
            created_at_utc=row["created_at_utc"], updated_at_utc=row["updated_at_utc"],
        )

    @staticmethod
    def _numeric(value: object | None) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _text(value: str, field: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field} must not be blank")
        return normalized

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None
