"""PostgreSQL persistence for portfolio workflows, reports, and operations."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .portfolio_interfaces import (
    CashBalanceReportRecord,
    CostSummaryReportRecord,
    CorporateActionManualCaseRecord,
    DiagnosticArchiveRecord,
    IngestionSloRecord,
    InstrumentPnlReportRecord,
    InstrumentRecord,
    LabelPnlReportRecord,
    LabelRecord,
    NoteRecord,
    PortfolioSummaryReportRecord,
    ProvenanceRecord,
    ReconciliationSourceRecord,
    SecuritiesCommissionSummaryReportRecord,
    TransferReportRecord,
    TransferSummaryReportRecord,
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

    def db_label_update(
        self, label_id: UUID, name: str | None, color: str | None, *, update_color: bool = False
    ) -> LabelRecord | None:
        try:
            with self._engine.begin() as connection:
                row = connection.execute(
                    text(
                        "UPDATE label SET name=COALESCE(:name, name), "
                        "color=CASE WHEN :update_color THEN :color ELSE color END, "
                        "updated_at_utc=now() WHERE label_id=:label_id RETURNING *"
                    ),
                    {"label_id": label_id, "name": self._optional_text(name), "color": self._optional_text(color),
                     "update_color": update_color or color is not None},
                ).mappings().one_or_none()
        except IntegrityError as error:
            raise ValueError("label name already exists") from error
        except SQLAlchemyError as error:
            raise RuntimeError("label update failed") from error
        return None if row is None else self._label(row)

    def db_label_delete(self, label_id: UUID) -> bool:
        try:
            with self._engine.begin() as connection:
                result = connection.execute(text("DELETE FROM label WHERE label_id=:id"), {"id": label_id})
        except IntegrityError as error:
            raise ValueError("label has notes without another target; remove those notes before deleting the label") from error
        except SQLAlchemyError as error:
            raise RuntimeError("label delete failed") from error
        return bool(result.rowcount)

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
                        "SELECT c.*, i.symbol, e.report_date_local, e.description, e.requires_manual, e.action_id, "
                        "e.reorg_code AS current_action_type, "
                        "(c.instrument_id=e.instrument_id AND i.conid=e.conid) AS correction_identity_valid "
                        "FROM corporate_action_manual_case c JOIN instrument i USING (instrument_id) "
                        "JOIN event_corp_action e USING (event_corp_action_id) "
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
                        "UPDATE event_corp_action SET provisional=requires_manual OR EXISTS (SELECT 1 FROM corporate_action_manual_case c "
                        "WHERE c.event_corp_action_id=event_corp_action.event_corp_action_id AND c.status='open') "
                        "WHERE instrument_id=:instrument_id"
                    ),
                    {"instrument_id": instrument_id},
                )
                connection.execute(
                    text(
                        "UPDATE pnl_snapshot_daily SET provisional=calculation_provisional OR EXISTS "
                        "(SELECT 1 FROM corporate_action_manual_case c "
                        "WHERE c.instrument_id=:instrument_id AND c.status='open') OR EXISTS "
                        "(SELECT 1 FROM event_corp_action e WHERE e.instrument_id=:instrument_id "
                        "AND e.requires_manual) WHERE instrument_id=:instrument_id"
                    ),
                    {"instrument_id": instrument_id},
                )
                full_row = connection.execute(
                    text(
                        "SELECT c.*, i.symbol, e.report_date_local, e.description, e.requires_manual, e.action_id, "
                        "e.reorg_code AS current_action_type, "
                        "(c.instrument_id=e.instrument_id AND i.conid=e.conid) AS correction_identity_valid "
                        "FROM corporate_action_manual_case c JOIN instrument i USING (instrument_id) "
                        "JOIN event_corp_action e USING (event_corp_action_id) "
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
            "WHERE s.account_id=:account_id AND UPPER(BTRIM(i.asset_category)) NOT IN ('CASH', 'FX') "
            "AND (CAST(:date_from AS date) IS NULL OR s.report_date_local>=:date_from) "
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

    def db_report_portfolio_summary(self, account_id: str) -> PortfolioSummaryReportRecord:
        eligible_artifacts = (
            "SELECT artifact.* FROM raw_artifact artifact "
            "JOIN ingestion_run owner ON owner.ingestion_run_id=artifact.ingestion_run_id "
            "LEFT JOIN ingestion_run completion ON completion.ingestion_run_id=artifact.completed_ingestion_run_id "
            "WHERE artifact.account_id=:account_id AND ((artifact.completed_ingestion_run_id IS NOT NULL "
            "AND completion.status='success') OR (artifact.completed_ingestion_run_id IS NULL AND owner.status='success'))"
        )
        cash_query = text(
            "WITH eligible_artifacts AS (" + eligible_artifacts + "), selected_artifact AS ("
            "SELECT artifact.raw_artifact_id, artifact.report_date_local FROM eligible_artifacts artifact "
            "WHERE artifact.report_date_local IS NOT NULL AND EXISTS (SELECT 1 FROM raw_record raw "
            "WHERE raw.raw_artifact_id=artifact.raw_artifact_id AND raw.section_name='CashReport') "
            "ORDER BY artifact.report_date_local DESC, artifact.created_at_utc DESC, artifact.raw_artifact_id DESC LIMIT 1"
            "), ranked AS (SELECT UPPER(BTRIM(raw.source_payload->>'currency')) AS currency, "
            "BTRIM(raw.source_payload->>'endingCash') AS ending_cash, "
            "row_number() OVER (PARTITION BY UPPER(BTRIM(raw.source_payload->>'currency')) "
            "ORDER BY raw.created_at_utc DESC, raw.raw_record_id DESC) AS row_rank "
            "FROM selected_artifact selected JOIN raw_record raw "
            "ON raw.raw_artifact_id=selected.raw_artifact_id "
            "WHERE raw.section_name='CashReport' AND BTRIM(COALESCE(raw.source_payload->>'currency', ''))<>'') "
            "SELECT selected.report_date_local, ranked.currency, ranked.ending_cash FROM selected_artifact selected "
            "LEFT JOIN ranked ON ranked.row_rank=1 ORDER BY ranked.currency NULLS LAST"
        )
        position_query = text(
            "WITH eligible_artifacts AS (" + eligible_artifacts + "), selected_artifact AS ("
            "SELECT artifact.raw_artifact_id, artifact.report_date_local FROM eligible_artifacts artifact "
            "WHERE artifact.report_date_local IS NOT NULL AND EXISTS (SELECT 1 FROM raw_record raw "
            "WHERE raw.raw_artifact_id=artifact.raw_artifact_id AND raw.section_name='OpenPositions') "
            "ORDER BY artifact.report_date_local DESC, artifact.created_at_utc DESC, artifact.raw_artifact_id DESC LIMIT 1"
            "), latest_positions AS (SELECT DISTINCT ON (raw.source_payload->>'conid') raw.source_payload "
            "FROM selected_artifact selected JOIN raw_record raw ON raw.raw_artifact_id=selected.raw_artifact_id "
            "WHERE raw.section_name='OpenPositions' AND BTRIM(COALESCE(raw.source_payload->>'conid', ''))<>'' "
            "AND UPPER(BTRIM(COALESCE(raw.source_payload->>'assetCategory', ''))) NOT IN ('CASH', 'FX') "
            "ORDER BY raw.source_payload->>'conid', raw.created_at_utc DESC, raw.raw_record_id DESC), normalized AS ("
            "SELECT REPLACE(BTRIM(source_payload->>'positionValue'), ',', '') AS position_value_text, "
            "REPLACE(BTRIM(source_payload->>'fxRateToBase'), ',', '') AS fx_rate_text, "
            "UPPER(BTRIM(source_payload->>'currency')) AS currency FROM latest_positions), valued AS ("
            "SELECT CASE WHEN position_value_text ~ '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$' "
            "THEN position_value_text::numeric ELSE NULL END AS position_value, "
            "CASE WHEN currency='USD' THEN 1::numeric WHEN fx_rate_text ~ '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$' "
            "AND fx_rate_text::numeric>0 THEN fx_rate_text::numeric ELSE NULL END AS fx_rate, "
            "1 AS row_present FROM normalized) "
            "SELECT selected.report_date_local, true AS open_positions_present, "
            "CASE WHEN count(*) FILTER (WHERE row_present=1 AND "
            "(position_value IS NULL OR fx_rate IS NULL))>0 "
            "THEN NULL ELSE COALESCE(sum(position_value*fx_rate), 0) END AS position_value_usd, "
            "count(*) FILTER (WHERE row_present=1 AND (position_value IS NULL OR fx_rate IS NULL)) "
            "AS missing_value_count "
            "FROM selected_artifact selected LEFT JOIN valued ON true GROUP BY selected.report_date_local"
        )
        transfer_query = text(
            "SELECT event.report_date_local, event.amount, event.amount_in_base, "
            "UPPER(BTRIM(event.currency)) AS currency, UPPER(BTRIM(event.functional_currency)) AS functional_currency, "
            "BTRIM(raw.source_payload->>'fxRateToBase') AS fx_rate_to_base, "
            "NULLIF(BTRIM(raw.source_payload->>'description'), '') AS description "
            "FROM event_cashflow event JOIN raw_record raw ON raw.raw_record_id=event.source_raw_record_id "
            "WHERE event.account_id=:account_id AND event.cash_action='Deposits/Withdrawals' "
            "ORDER BY event.report_date_local DESC, event.event_cashflow_id DESC"
        )
        cost_query = text(
            "WITH eligible_artifacts AS (" + eligible_artifacts + "), trade_cost_events AS ("
            "SELECT CASE WHEN UPPER(BTRIM(instrument.asset_category)) IN ('CASH', 'FX') "
            "THEN 'FX conversion commissions' ELSE 'Securities commissions' END AS category, "
            "CASE WHEN UPPER(BTRIM(event.functional_currency))<>'USD' THEN NULL "
            "WHEN COALESCE(NULLIF(UPPER(BTRIM(raw.source_payload->>'ibCommissionCurrency')), ''), "
            "UPPER(BTRIM(event.currency)))='USD' THEN -event.commission "
            "WHEN COALESCE(NULLIF(UPPER(BTRIM(raw.source_payload->>'ibCommissionCurrency')), ''), "
            "UPPER(BTRIM(event.currency)))=UPPER(BTRIM(event.currency)) "
            "AND event.fx_rate_to_base>0 THEN -event.commission*event.fx_rate_to_base ELSE NULL END AS net_cost_usd, "
            "UPPER(BTRIM(instrument.asset_category)) NOT IN ('CASH', 'FX') AS included_in_instrument_pnl, "
            "event.report_date_local AS activity_date FROM event_trade_fill event "
            "JOIN instrument ON instrument.instrument_id=event.instrument_id "
            "JOIN raw_record raw ON raw.raw_record_id=event.source_raw_record_id "
            "WHERE event.account_id=:account_id AND COALESCE(event.commission, 0)<>0"
            "), cashflow_cost_events AS ("
            "SELECT CASE WHEN event.cash_action='Withholding Tax' THEN 'Dividend withholding tax' "
            "WHEN event.cash_action='Other Fees' AND event.instrument_id IS NOT NULL "
            "THEN 'Instrument-related other fees' "
            "WHEN event.cash_action='Other Fees' THEN 'Account-level other fees' "
            "ELSE event.cash_action END AS category, "
            "CASE WHEN UPPER(BTRIM(event.functional_currency))<>'USD' THEN NULL "
            "WHEN event.amount_in_base IS NOT NULL THEN -event.amount_in_base "
            "WHEN UPPER(BTRIM(event.currency))='USD' THEN -event.amount "
            "WHEN BTRIM(COALESCE(raw.source_payload->>'fxRateToBase', '')) "
            "~ '^[+]?[0-9]+([.][0-9]+)?$' "
            "AND (raw.source_payload->>'fxRateToBase')::numeric>0 "
            "THEN -event.amount*(raw.source_payload->>'fxRateToBase')::numeric ELSE NULL END AS net_cost_usd, "
            "CASE WHEN event.cash_action='Withholding Tax' THEN true "
            "WHEN event.cash_action='Other Fees' THEN event.instrument_id IS NOT NULL "
            "ELSE false END AS included_in_instrument_pnl, "
            "event.report_date_local AS activity_date FROM event_cashflow event "
            "JOIN raw_record raw ON raw.raw_record_id=event.source_raw_record_id "
            "WHERE event.account_id=:account_id AND event.cash_action<>'Deposits/Withdrawals' "
            "AND (LOWER(event.cash_action) LIKE '%interest%' OR LOWER(event.cash_action) LIKE '%fee%' "
            "OR LOWER(event.cash_action) LIKE '%tax%')"
            "), transaction_tax_candidates AS ("
            "SELECT raw.source_payload, artifact.report_date_local, raw.created_at_utc, raw.raw_record_id, "
            "COALESCE(NULLIF(BTRIM(raw.source_payload->>'tradeId'), ''), "
            "NULLIF(BTRIM(raw.source_payload->>'tradeID'), ''), "
            "(raw.source_payload-'reportDate')::text) AS event_identity, "
            "row_number() OVER (PARTITION BY artifact.raw_artifact_id, "
            "COALESCE(NULLIF(BTRIM(raw.source_payload->>'tradeId'), ''), "
            "NULLIF(BTRIM(raw.source_payload->>'tradeID'), ''), "
            "(raw.source_payload-'reportDate')::text) ORDER BY raw.source_row_ref, raw.raw_record_id) "
            "AS event_occurrence FROM eligible_artifacts artifact JOIN raw_record raw "
            "ON raw.raw_artifact_id=artifact.raw_artifact_id WHERE raw.section_name='TransactionTaxes'"
            "), ranked_transaction_taxes AS ("
            "SELECT DISTINCT ON (event_identity, event_occurrence) source_payload, report_date_local, "
            "created_at_utc, raw_record_id FROM transaction_tax_candidates "
            "ORDER BY event_identity, event_occurrence, report_date_local DESC, created_at_utc DESC, raw_record_id DESC"
            "), transaction_tax_events AS ("
            "SELECT COALESCE(NULLIF(BTRIM(source_payload->>'taxDescription'), ''), 'Transaction tax') AS category, "
            "CASE WHEN BTRIM(COALESCE(source_payload->>'taxAmount', '')) "
            "!~ '^[+-]?[0-9]+([.][0-9]+)?$' THEN NULL "
            "WHEN UPPER(BTRIM(source_payload->>'currency'))='USD' "
            "THEN -(source_payload->>'taxAmount')::numeric "
            "WHEN BTRIM(COALESCE(source_payload->>'fxRateToBase', '')) ~ '^[+]?[0-9]+([.][0-9]+)?$' "
            "AND (source_payload->>'fxRateToBase')::numeric>0 "
            "THEN -(source_payload->>'taxAmount')::numeric*(source_payload->>'fxRateToBase')::numeric "
            "ELSE NULL END AS net_cost_usd, false AS included_in_instrument_pnl, "
            "CASE WHEN BTRIM(COALESCE(source_payload->>'reportDate', '')) ~ '^[0-9]{8}$' "
            "THEN to_date(source_payload->>'reportDate', 'YYYYMMDD') ELSE report_date_local END AS activity_date "
            "FROM ranked_transaction_taxes"
            "), cost_events AS ("
            "SELECT * FROM trade_cost_events UNION ALL SELECT * FROM cashflow_cost_events "
            "UNION ALL SELECT * FROM transaction_tax_events"
            ") SELECT category, sum(net_cost_usd) AS net_cost_usd, included_in_instrument_pnl, "
            "count(*) FILTER (WHERE net_cost_usd IS NULL) AS missing_value_count, "
            "min(activity_date) AS activity_date_from, max(activity_date) AS activity_date_to "
            "FROM cost_events GROUP BY category, included_in_instrument_pnl ORDER BY category"
        )
        dividend_query = text(
            "WITH dividend_events AS (SELECT event.cash_action, event.report_date_local AS activity_date, "
            "CASE WHEN UPPER(BTRIM(event.functional_currency))<>'USD' THEN NULL "
            "WHEN event.amount_in_base IS NOT NULL THEN event.amount_in_base "
            "WHEN UPPER(BTRIM(event.currency))='USD' THEN event.amount "
            "WHEN BTRIM(COALESCE(raw.source_payload->>'fxRateToBase', '')) "
            "~ '^[+]?[0-9]+([.][0-9]+)?$' "
            "AND (raw.source_payload->>'fxRateToBase')::numeric>0 "
            "THEN event.amount*(raw.source_payload->>'fxRateToBase')::numeric ELSE NULL END AS amount_usd "
            "FROM event_cashflow event JOIN raw_record raw ON raw.raw_record_id=event.source_raw_record_id "
            "WHERE event.account_id=:account_id AND event.cash_action IN "
            "('Dividends', 'Payment In Lieu Of Dividends', 'Withholding Tax')) "
            "SELECT min(activity_date) AS activity_date_from, max(activity_date) AS activity_date_to, "
            "COALESCE(sum(amount_usd) FILTER (WHERE cash_action IN "
            "('Dividends', 'Payment In Lieu Of Dividends')), 0) AS gross_dividend_payments_usd, "
            "COALESCE(-sum(amount_usd) FILTER (WHERE cash_action='Withholding Tax'), 0) "
            "AS dividend_withholding_tax_usd, count(*) FILTER (WHERE amount_usd IS NULL AND cash_action IN "
            "('Dividends', 'Payment In Lieu Of Dividends')) AS gross_missing_value_count, "
            "count(*) FILTER (WHERE amount_usd IS NULL AND cash_action='Withholding Tax') "
            "AS withholding_missing_value_count "
            "FROM dividend_events"
        )
        commission_query = text(
            "WITH securities_commission_events AS ("
            "SELECT CASE UPPER(BTRIM(instrument.asset_category)) "
            "WHEN 'STK' THEN 'Stocks' WHEN 'OPT' THEN 'Options' "
            "ELSE INITCAP(REPLACE(BTRIM(instrument.asset_category), '_', ' ')) END AS instrument_type, "
            "event.side, event.instrument_id, event.report_date_local AS activity_date, "
            "CASE WHEN UPPER(BTRIM(event.functional_currency))<>'USD' THEN NULL "
            "WHEN COALESCE(NULLIF(UPPER(BTRIM(raw.source_payload->>'ibCommissionCurrency')), ''), "
            "UPPER(BTRIM(event.currency)))='USD' THEN -event.commission "
            "WHEN COALESCE(NULLIF(UPPER(BTRIM(raw.source_payload->>'ibCommissionCurrency')), ''), "
            "UPPER(BTRIM(event.currency)))=UPPER(BTRIM(event.currency)) "
            "AND event.fx_rate_to_base>0 THEN -event.commission*event.fx_rate_to_base ELSE NULL END "
            "AS commission_usd FROM event_trade_fill event "
            "JOIN instrument ON instrument.instrument_id=event.instrument_id "
            "JOIN raw_record raw ON raw.raw_record_id=event.source_raw_record_id "
            "WHERE event.account_id=:account_id AND COALESCE(event.commission, 0)<>0 "
            "AND UPPER(BTRIM(instrument.asset_category)) NOT IN ('CASH', 'FX')"
            "), grouped AS ("
            "SELECT instrument_type, side, count(*) AS execution_count, "
            "count(DISTINCT instrument_id) AS instrument_count, sum(commission_usd) AS commission_usd, "
            "count(*) FILTER (WHERE commission_usd IS NULL) AS missing_value_count, "
            "min(activity_date) AS activity_date_from, max(activity_date) AS activity_date_to, false AS is_total "
            "FROM securities_commission_events GROUP BY instrument_type, side"
            "), overall AS ("
            "SELECT NULL::text AS instrument_type, NULL::text AS side, count(*) AS execution_count, "
            "count(DISTINCT instrument_id) AS instrument_count, sum(commission_usd) AS commission_usd, "
            "count(*) FILTER (WHERE commission_usd IS NULL) AS missing_value_count, "
            "min(activity_date) AS activity_date_from, max(activity_date) AS activity_date_to, true AS is_total "
            "FROM securities_commission_events"
            ") SELECT * FROM grouped UNION ALL SELECT * FROM overall "
            "ORDER BY is_total, instrument_type, side"
        )
        params = {"account_id": self._text(account_id, "account_id")}
        try:
            with self._engine.connect() as connection:
                cash_rows = connection.execute(cash_query, params).mappings().all()
                position_rows = connection.execute(position_query, params).mappings().all()
                transfer_rows = connection.execute(transfer_query, params).mappings().all()
                cost_rows = connection.execute(cost_query, params).mappings().all()
                dividend_rows = connection.execute(dividend_query, params).mappings().all()
                commission_rows = connection.execute(commission_query, params).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("portfolio summary report failed") from error

        report_date_local = None if not cash_rows else cash_rows[0]["report_date_local"]
        base_cash_usd: Decimal | None = None
        cash_balances = []
        for row in cash_rows:
            currency = row["currency"]
            if currency is None:
                continue
            ending_cash = self._decimal_or_none(row["ending_cash"])
            if currency == "BASE_SUMMARY":
                base_cash_usd = ending_cash
            else:
                cash_balances.append(CashBalanceReportRecord(
                    currency=currency, amount=None if ending_cash is None else str(ending_cash)
                ))

        position_row = None if not position_rows else position_rows[0]
        estimated_nlv_usd = None
        if (
            base_cash_usd is not None
            and position_row is not None
            and position_row["report_date_local"] == report_date_local
            and bool(position_row["open_positions_present"])
            and int(position_row["missing_value_count"]) == 0
            and position_row["position_value_usd"] is not None
        ):
            estimated_nlv_usd = base_cash_usd + Decimal(position_row["position_value_usd"])

        transfer_totals: dict[str, dict[str, Decimal]] = {}
        transfers = []
        net_transfers_usd = Decimal("0")
        transfers_have_usd_values = True
        for row in transfer_rows:
            amount = Decimal(row["amount"])
            currency = row["currency"]
            totals = transfer_totals.setdefault(
                currency,
                {"net": Decimal("0"), "deposits": Decimal("0"), "withdrawals": Decimal("0")},
            )
            totals["net"] += amount
            transfer_type = "Deposit" if amount >= 0 else "Withdrawal"
            if amount >= 0:
                totals["deposits"] += amount
            else:
                totals["withdrawals"] += abs(amount)
            transfers.append(
                TransferReportRecord(
                    report_date_local=row["report_date_local"], transfer_type=transfer_type, amount=str(abs(amount)),
                    currency=currency, description=row["description"],
                )
            )

            amount_in_base = row["amount_in_base"]
            if row["functional_currency"] != "USD":
                transfers_have_usd_values = False
            elif amount_in_base is not None:
                net_transfers_usd += Decimal(amount_in_base)
            elif currency == "USD":
                net_transfers_usd += amount
            elif (fx_rate := self._decimal_or_none(row["fx_rate_to_base"])) is not None and fx_rate > 0:
                net_transfers_usd += amount * fx_rate
            else:
                transfers_have_usd_values = False

        transfer_summaries = tuple(
            TransferSummaryReportRecord(
                currency=currency,
                net_transfers=str(totals["net"]),
                gross_deposits=str(totals["deposits"]),
                gross_withdrawals=str(totals["withdrawals"]),
            )
            for currency, totals in sorted(transfer_totals.items())
        )

        cost_summary = tuple(
            CostSummaryReportRecord(
                category=row["category"],
                net_cost_usd=(
                    None if int(row["missing_value_count"]) > 0 or row["net_cost_usd"] is None
                    else str(row["net_cost_usd"])
                ),
                included_in_instrument_pnl=bool(row["included_in_instrument_pnl"]),
            )
            for row in sorted(cost_rows, key=lambda item: item["category"])
        )
        costs_complete = all(
            int(row["missing_value_count"]) == 0 and row["net_cost_usd"] is not None
            for row in cost_rows
        )
        total_costs_usd = (
            sum((Decimal(row["net_cost_usd"]) for row in cost_rows), Decimal("0"))
            if costs_complete else None
        )
        outside_cost_rows = [row for row in cost_rows if not bool(row["included_in_instrument_pnl"])]
        outside_costs_complete = all(
            int(row["missing_value_count"]) == 0 and row["net_cost_usd"] is not None
            for row in outside_cost_rows
        )
        costs_outside_instrument_pnl_usd = (
            sum((Decimal(row["net_cost_usd"]) for row in outside_cost_rows), Decimal("0"))
            if outside_costs_complete else None
        )

        dividend_row = None if not dividend_rows else dividend_rows[0]
        gross_dividend_payments_usd: Decimal | None
        dividend_withholding_tax_usd: Decimal | None
        if dividend_row is None:
            gross_dividend_payments_usd = Decimal("0")
            dividend_withholding_tax_usd = Decimal("0")
        else:
            gross_dividend_payments_usd = (
                None
                if int(dividend_row["gross_missing_value_count"]) > 0
                or dividend_row["gross_dividend_payments_usd"] is None
                else Decimal(dividend_row["gross_dividend_payments_usd"])
            )
            dividend_withholding_tax_usd = (
                None
                if int(dividend_row["withholding_missing_value_count"]) > 0
                or dividend_row["dividend_withholding_tax_usd"] is None
                else Decimal(dividend_row["dividend_withholding_tax_usd"])
            )
        net_dividend_payments_usd = (
            gross_dividend_payments_usd - dividend_withholding_tax_usd
            if gross_dividend_payments_usd is not None and dividend_withholding_tax_usd is not None else None
        )

        commission_total_row = next(
            (row for row in commission_rows if bool(row["is_total"])),
            None,
        )
        securities_commission_summary = tuple(
            SecuritiesCommissionSummaryReportRecord(
                instrument_type=row["instrument_type"],
                side=row["side"],
                execution_count=int(row["execution_count"]),
                instrument_count=int(row["instrument_count"]),
                commission_usd=(
                    None if int(row["missing_value_count"]) > 0 or row["commission_usd"] is None
                    else str(row["commission_usd"])
                ),
            )
            for row in sorted(
                (row for row in commission_rows if not bool(row["is_total"])),
                key=lambda item: (item["instrument_type"], item["side"]),
            )
        )
        securities_commission_date_from = (
            None if commission_total_row is None else commission_total_row["activity_date_from"]
        )
        securities_commission_date_to = (
            None if commission_total_row is None else commission_total_row["activity_date_to"]
        )
        securities_commission_execution_count = (
            0 if commission_total_row is None else int(commission_total_row["execution_count"])
        )
        securities_commission_instrument_count = (
            0 if commission_total_row is None else int(commission_total_row["instrument_count"])
        )
        if commission_total_row is None or int(commission_total_row["execution_count"]) == 0:
            securities_commission_total_usd: Decimal | None = Decimal("0")
        elif (
            int(commission_total_row["missing_value_count"]) > 0
            or commission_total_row["commission_usd"] is None
        ):
            securities_commission_total_usd = None
        else:
            securities_commission_total_usd = Decimal(commission_total_row["commission_usd"])

        activity_starts = [
            value for value in (
                *(row["activity_date_from"] for row in cost_rows),
                None if dividend_row is None else dividend_row["activity_date_from"],
            ) if value is not None
        ]
        activity_ends = [
            value for value in (
                *(row["activity_date_to"] for row in cost_rows),
                None if dividend_row is None else dividend_row["activity_date_to"],
                report_date_local,
            ) if value is not None
        ] if activity_starts else []
        activity_date_from = min(activity_starts) if activity_starts else None
        activity_date_to = max(activity_ends) if activity_ends else None

        net_transfers_value = net_transfers_usd if transfers_have_usd_values else None
        total_profit_usd = (
            None if estimated_nlv_usd is None or net_transfers_value is None
            else estimated_nlv_usd - net_transfers_value
        )
        profit_percent = None
        if total_profit_usd is not None and net_transfers_value is not None and net_transfers_value > 0:
            profit_percent = (total_profit_usd / net_transfers_value * Decimal("100")).quantize(Decimal("0.00000001"))

        return PortfolioSummaryReportRecord(
            report_date_local=report_date_local,
            cash_balances=tuple(cash_balances),
            transfer_summary_by_currency=transfer_summaries,
            transfers=tuple(transfers),
            activity_date_from=activity_date_from,
            activity_date_to=activity_date_to,
            cost_summary=cost_summary,
            total_costs_usd=None if total_costs_usd is None else str(total_costs_usd),
            costs_outside_instrument_pnl_usd=(
                None if costs_outside_instrument_pnl_usd is None else str(costs_outside_instrument_pnl_usd)
            ),
            gross_dividend_payments_usd=(
                None if gross_dividend_payments_usd is None else str(gross_dividend_payments_usd)
            ),
            dividend_withholding_tax_usd=(
                None if dividend_withholding_tax_usd is None else str(dividend_withholding_tax_usd)
            ),
            net_dividend_payments_usd=(
                None if net_dividend_payments_usd is None else str(net_dividend_payments_usd)
            ),
            securities_commission_summary=securities_commission_summary,
            securities_commission_date_from=securities_commission_date_from,
            securities_commission_date_to=securities_commission_date_to,
            securities_commission_execution_count=securities_commission_execution_count,
            securities_commission_instrument_count=securities_commission_instrument_count,
            securities_commission_total_usd=(
                None if securities_commission_total_usd is None else str(securities_commission_total_usd)
            ),
            net_transfers_usd=None if net_transfers_value is None else str(net_transfers_value),
            estimated_net_liquidation_value_usd=None if estimated_nlv_usd is None else str(estimated_nlv_usd),
            total_profit_usd=None if total_profit_usd is None else str(total_profit_usd),
            profit_percent=None if profit_percent is None else str(profit_percent),
        )

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
                "raw.section_name, raw.source_row_ref, raw.source_payload, raw.raw_artifact_id, "
                "raw.ingestion_run_id FROM " + table + " event "
                "JOIN raw_record raw ON raw.raw_record_id=event.source_raw_record_id WHERE event.account_id=:account_id "
                "AND event.report_date_local<=:report_date AND event.instrument_id=:instrument_id"
            )
        unions.append(
            "SELECT 'open_position' AS event_type, raw.raw_record_id AS event_id, "
            "raw.raw_record_id AS source_raw_record_id, raw.section_name, raw.source_row_ref, "
            "raw.source_payload, raw.raw_artifact_id, raw.ingestion_run_id "
            "FROM pnl_snapshot_daily s JOIN instrument i USING (instrument_id) "
            "JOIN LATERAL (SELECT position.* FROM raw_record position "
            "WHERE position.account_id=s.account_id AND position.ingestion_run_id=s.ingestion_run_id "
            "AND position.section_name='OpenPositions' "
            "AND position.source_row_ref LIKE 'OpenPositions:OpenPosition:%' "
            "AND position.source_payload->>'conid'=i.conid "
            "AND UPPER(BTRIM(position.source_payload->>'assetCategory')) NOT IN ('CASH', 'FX') "
            "ORDER BY position.raw_record_id DESC LIMIT 1) raw ON true "
            "WHERE s.account_id=:account_id AND s.report_date_local=:report_date "
            "AND s.instrument_id=:instrument_id"
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
        def raw_numeric(field: str, alias: str = "raw") -> str:
            return (
                f"CASE WHEN BTRIM(COALESCE({alias}.source_payload->>'{field}', '')) "
                "IN ('', '-', '--', 'N/A') THEN NULL ELSE "
                f"REPLACE(BTRIM({alias}.source_payload->>'{field}'), ',', '')::numeric END"
            )

        raw_currency = "UPPER(BTRIM(raw.source_payload->>'currency'))"
        raw_fx_rate = raw_numeric("fxRateToBase")
        row_fx_rate = (
            f"CASE WHEN {raw_currency}=s.currency THEN 1::numeric ELSE {raw_fx_rate} END"
        )
        def conversion_rate(currency: str) -> str:
            return (
                f"CASE WHEN {currency}=s.currency THEN 1::numeric ELSE "
                "(SELECT fx.fx_rate FROM event_fx fx WHERE fx.account_id=s.account_id "
                f"AND fx.currency={currency} AND fx.functional_currency=s.currency "
                "AND fx.report_date_local<=event.report_date_local AND fx.fx_rate>0 "
                "ORDER BY fx.report_date_local DESC, fx.ingestion_run_id DESC, "
                "fx.source_raw_record_id DESC LIMIT 1) END"
            )

        event_conversion = conversion_rate("event.currency")
        trade_fx_rate = (
            "CASE WHEN event.currency=s.currency THEN 1::numeric ELSE COALESCE("
            "CASE WHEN event.fx_rate_to_base>0 THEN event.fx_rate_to_base END, "
            "CASE WHEN event.net_cash_in_base<>0 THEN ABS(event.net_cash_in_base / NULLIF(event.net_cash, 0)) END, "
            f"({event_conversion})) END"
        )
        commission_currency = (
            "COALESCE(NULLIF(UPPER(BTRIM(raw.source_payload->>'ibCommissionCurrency')), ''), event.currency)"
        )
        commission_fx_rate = (
            f"CASE WHEN {commission_currency}=event.currency THEN ({trade_fx_rate}) "
            f"ELSE ({conversion_rate(commission_currency)}) END"
        )
        position = raw_numeric("position")
        unrealized = raw_numeric("fifoPnlUnrealized")
        # Canonical identities deduplicate overlapping reports; amounts remain broker-reported.
        trade_history = (
            "FROM event_trade_fill event JOIN raw_record raw ON raw.raw_record_id=event.source_raw_record_id "
            "WHERE event.account_id=s.account_id AND event.instrument_id=s.instrument_id "
            "AND event.report_date_local<=s.report_date_local "
        )
        cash_history = (
            "FROM event_cashflow event WHERE event.account_id=s.account_id "
            "AND event.instrument_id=s.instrument_id AND event.report_date_local<=s.report_date_local "
        )

        def converted_expense(amount: str, rate: str) -> str:
            # Zero expenses need no FX, but a missing rate for a nonzero expense stays unknown.
            return f"CASE WHEN COALESCE({amount}, 0)=0 THEN 0 ELSE ({amount}) * ({rate}) END"

        def complete_total(value: str, history: str) -> str:
            return (
                "(SELECT CASE WHEN count(*)=0 THEN 0 WHEN bool_and(value IS NOT NULL) THEN sum(value) END "
                f"FROM (SELECT ({value}) AS value {history}) amounts)"
            )

        trade_fees = (
            f"({converted_expense('ABS(event.commission)', commission_fx_rate)}) + "
            f"({converted_expense('ABS(event.fees)', trade_fx_rate)})"
        )
        cash_fees = converted_expense("event.fees", event_conversion)
        cash_tax = converted_expense("event.withholding_tax", event_conversion)
        cash_amount = (
            "CASE WHEN event.amount_in_base IS NOT NULL AND event.functional_currency=s.currency "
            f"THEN event.amount_in_base ELSE event.amount * ({event_conversion}) END"
        )
        cash_net = f"({cash_amount}) - ({cash_fees}) - ({cash_tax})"
        cash_reported_fees = f"({cash_fees}) - CASE WHEN event.cash_action='Other Fees' THEN ({cash_amount}) ELSE 0 END"
        cash_reported_tax = (
            f"({cash_tax}) - CASE WHEN event.cash_action='Withholding Tax' THEN ({cash_amount}) ELSE 0 END"
        )
        broker_realized = (
            f"{complete_total(f'event.realized_pnl * ({trade_fx_rate})', trade_history)} + "
            f"{complete_total(cash_net, cash_history)}"
        )
        broker_fees = f"{complete_total(trade_fees, trade_history)} + {complete_total(cash_reported_fees, cash_history)}"
        broker_tax = complete_total(cash_reported_tax, cash_history)

        # Empty sections have a persisted sentinel; require successful processing of this source lineage.
        complete_positions = (
            "EXISTS(SELECT 1 FROM raw_record section "
            "JOIN ingestion_run run ON run.ingestion_run_id=section.ingestion_run_id "
            "LEFT JOIN raw_artifact artifact ON artifact.raw_artifact_id=section.raw_artifact_id "
            "LEFT JOIN ingestion_run completion ON completion.ingestion_run_id=artifact.completed_ingestion_run_id "
            "WHERE section.account_id=s.account_id AND section.ingestion_run_id=s.ingestion_run_id "
            "AND section.report_date_local=s.report_date_local AND section.section_name='OpenPositions' "
            "AND (completion.status='success' "
            "OR (artifact.completed_ingestion_run_id IS NULL AND run.status='success')))"
        )
        absent_position = (
            "NOT EXISTS(SELECT 1 FROM raw_record present WHERE present.account_id=s.account_id "
            "AND present.ingestion_run_id=s.ingestion_run_id AND present.report_date_local=s.report_date_local "
            "AND present.section_name='OpenPositions' AND present.source_payload->>'conid'=i.conid)"
        )
        closed_position = f"CASE WHEN ({complete_positions}) AND ({absent_position}) THEN 0::numeric END"

        query = text(
            "SELECT s.report_date_local, s.instrument_id, i.conid, i.symbol, s.currency, s.position_qty, "
            "s.realized_pnl, s.unrealized_pnl, s.fees, s.withholding_tax, s.provisional, "
            f"COALESCE((SELECT {position} FROM raw_record raw WHERE raw.account_id=s.account_id "
            "AND raw.ingestion_run_id=s.ingestion_run_id "
            "AND raw.section_name='OpenPositions' AND raw.report_date_local=s.report_date_local "
            "AND raw.source_payload->>'conid'=i.conid ORDER BY raw.raw_record_id DESC LIMIT 1), "
            f"{closed_position}) AS broker_position_qty, "
            f"({broker_realized}) AS broker_realized_pnl, "
            f"COALESCE((SELECT ({unrealized}) * ({row_fx_rate}) FROM raw_record raw "
            "WHERE raw.account_id=s.account_id AND raw.ingestion_run_id=s.ingestion_run_id "
            "AND raw.section_name='OpenPositions' AND raw.report_date_local=s.report_date_local "
            "AND raw.source_payload->>'conid'=i.conid ORDER BY raw.raw_record_id DESC LIMIT 1), "
            f"{closed_position}) AS broker_unrealized_pnl, "
            f"({broker_fees}) AS broker_fees, "
            f"{broker_tax} AS broker_withholding_tax, "
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
                        "WHERE account_id=:account_id AND run_type='scheduled' "
                        "AND started_at_utc>=:since_utc ORDER BY started_at_utc asc"
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
            case_id=row["case_id"], event_corp_action_id=row["event_corp_action_id"], action_type=row["current_action_type"],
            instrument_id=row["instrument_id"], symbol=row["symbol"], status=row["status"], owner=row["owner"],
            resolution_note=row["resolution_note"], resolved_at_utc=row["resolved_at_utc"],
            created_at_utc=row["created_at_utc"], updated_at_utc=row["updated_at_utc"],
            report_date_local=row["report_date_local"], description=row["description"], requires_manual=row["requires_manual"],
            action_id=row["action_id"],
            correction_identity_valid=bool(row["correction_identity_valid"]),
        )

    @staticmethod
    def _numeric(value: object | None) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _decimal_or_none(value: object | None) -> Decimal | None:
        if value is None:
            return None
        try:
            parsed = Decimal(str(value).strip().replace(",", ""))
        except (InvalidOperation, ValueError):
            return None
        return parsed if parsed.is_finite() else None

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
