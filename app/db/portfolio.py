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
                        "UPDATE pnl_snapshot_daily SET provisional=calculation_provisional OR EXISTS "
                        "(SELECT 1 FROM corporate_action_manual_case c "
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
        params = {"account_id": self._text(account_id, "account_id")}
        try:
            with self._engine.connect() as connection:
                cash_rows = connection.execute(cash_query, params).mappings().all()
                position_rows = connection.execute(position_query, params).mappings().all()
                transfer_rows = connection.execute(transfer_query, params).mappings().all()
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
                "raw.section_name, raw.source_row_ref, raw.source_payload FROM " + table + " event "
                "JOIN raw_record raw ON raw.raw_record_id=event.source_raw_record_id WHERE event.account_id=:account_id "
                "AND event.report_date_local<=:report_date AND event.instrument_id=:instrument_id"
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
        commission_currency = (
            "COALESCE(NULLIF(UPPER(BTRIM(raw.source_payload->>'ibCommissionCurrency')), ''), "
            f"{raw_currency})"
        )
        commission_conversion_rate = (
            f"(SELECT {raw_numeric('rate', 'fx_raw')} FROM raw_record fx_raw "
            "WHERE fx_raw.account_id=s.account_id AND fx_raw.ingestion_run_id=s.ingestion_run_id "
            "AND fx_raw.section_name='ConversionRates' "
            f"AND UPPER(BTRIM(fx_raw.source_payload->>'fromCurrency'))={commission_currency} "
            "AND COALESCE(NULLIF(UPPER(BTRIM(fx_raw.source_payload->>'toCurrency')), ''), s.currency)=s.currency "
            f"AND {raw_numeric('rate', 'fx_raw')} IS NOT NULL "
            "ORDER BY fx_raw.report_date_local DESC, fx_raw.raw_record_id DESC LIMIT 1)"
        )
        commission_fx_rate = (
            f"CASE WHEN {commission_currency}=s.currency THEN 1::numeric "
            f"WHEN {commission_currency}={raw_currency} THEN {row_fx_rate} "
            f"ELSE {commission_conversion_rate} END"
        )
        position = raw_numeric("position")
        realized = raw_numeric("fifoPnlRealized")
        unrealized = raw_numeric("fifoPnlUnrealized")
        commission = raw_numeric("ibCommission")
        fees = raw_numeric("fees")
        withholding_tax = raw_numeric("withholdingTax")

        query = text(
            "SELECT s.report_date_local, s.instrument_id, i.conid, i.symbol, s.currency, s.position_qty, "
            "s.realized_pnl, s.unrealized_pnl, s.fees, s.withholding_tax, s.provisional, "
            f"(SELECT {position} FROM raw_record raw WHERE raw.account_id=s.account_id "
            "AND raw.ingestion_run_id=s.ingestion_run_id "
            "AND raw.section_name='OpenPositions' AND raw.report_date_local=s.report_date_local "
            "AND raw.source_payload->>'conid'=i.conid ORDER BY raw.raw_record_id DESC LIMIT 1) AS broker_position_qty, "
            f"(SELECT CASE WHEN bool_and(({realized}) IS NULL OR ({row_fx_rate}) IS NOT NULL) "
            f"THEN sum(({realized}) * ({row_fx_rate})) ELSE NULL END "
            "FROM raw_record raw WHERE raw.account_id=s.account_id AND raw.ingestion_run_id=s.ingestion_run_id "
            "AND raw.section_name='Trades' AND raw.report_date_local=s.report_date_local "
            "AND raw.source_payload->>'conid'=i.conid) AS broker_realized_pnl, "
            f"(SELECT ({unrealized}) * ({row_fx_rate}) FROM raw_record raw "
            "WHERE raw.account_id=s.account_id AND raw.ingestion_run_id=s.ingestion_run_id "
            "AND raw.section_name='OpenPositions' AND raw.report_date_local=s.report_date_local "
            "AND raw.source_payload->>'conid'=i.conid ORDER BY raw.raw_record_id DESC LIMIT 1) AS broker_unrealized_pnl, "
            "(SELECT CASE WHEN bool_and((fee_row.commission IS NULL OR fee_row.commission_fx_rate IS NOT NULL) "
            "AND (fee_row.fees IS NULL OR fee_row.fee_fx_rate IS NOT NULL)) "
            "THEN sum(COALESCE(fee_row.commission * fee_row.commission_fx_rate, 0) + "
            "COALESCE(fee_row.fees * fee_row.fee_fx_rate, 0)) ELSE NULL END FROM ("
            f"SELECT ({commission}) AS commission, ({fees}) AS fees, "
            f"({commission_fx_rate}) AS commission_fx_rate, ({row_fx_rate}) AS fee_fx_rate "
            "FROM raw_record raw WHERE raw.account_id=s.account_id AND raw.ingestion_run_id=s.ingestion_run_id "
            "AND raw.section_name='Trades' AND raw.report_date_local=s.report_date_local "
            "AND raw.source_payload->>'conid'=i.conid) fee_row) AS broker_fees, "
            f"(SELECT CASE WHEN bool_and(({withholding_tax}) IS NULL OR ({row_fx_rate}) IS NOT NULL) "
            f"THEN sum(({withholding_tax}) * ({row_fx_rate})) ELSE NULL END "
            "FROM raw_record raw WHERE raw.account_id=s.account_id AND raw.ingestion_run_id=s.ingestion_run_id "
            "AND raw.section_name='CashTransactions' "
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
            case_id=row["case_id"], event_corp_action_id=row["event_corp_action_id"], action_type=row["action_type"],
            instrument_id=row["instrument_id"], symbol=row["symbol"], status=row["status"], owner=row["owner"],
            resolution_note=row["resolution_note"], resolved_at_utc=row["resolved_at_utc"],
            created_at_utc=row["created_at_utc"], updated_at_utc=row["updated_at_utc"],
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
