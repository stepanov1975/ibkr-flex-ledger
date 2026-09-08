"""Atomic, source-bound split corrections and rollback-only accounting previews."""

from decimal import Decimal
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from app.db.ingestion_run import SQLAlchemyIngestionRunService
from app.db.ledger_snapshot import SQLAlchemyLedgerSnapshotService
from app.ledger import StockLedgerSnapshotService


class SplitCorrectionConflict(ValueError):
    """The case or accounting inputs changed, or ingestion is still running."""


class SQLAlchemySplitCorrectionService:
    def __init__(self, engine: Engine, account_id: str):
        self._engine = engine
        self._account_id = account_id

    def preview_or_apply(
        self, case_id: UUID, new_shares: Decimal, old_shares: Decimal,
        note: str, preview_token: str | None = None,
    ) -> dict[str, Any]:
        """Rebuild in one transaction; commit only an unchanged, accepted preview."""
        if not all(value.is_finite() and value > 0 for value in (new_shares, old_shares)):
            raise ValueError("New and old share quantities must be positive finite numbers.")
        if not note.strip():
            raise ValueError("Record the broker evidence used to verify the ratio.")
        factor = new_shares / old_shares
        with self._engine.connect() as connection:
            with connection.begin() as transaction:
                self._lock_account(connection)
                params = {"case_id": case_id, "account_id": self._account_id}
                case = connection.execute(text(
                    "SELECT c.*, e.reorg_code, e.conid, e.report_date_local, e.source_raw_record_id, "
                    "e.action_id, e.requires_manual, r.source_payload, i.symbol, "
                    "e.instrument_id AS event_instrument_id, i.conid AS instrument_conid "
                    "FROM corporate_action_manual_case c JOIN event_corp_action e USING(event_corp_action_id) "
                    "JOIN raw_record r ON r.raw_record_id=e.source_raw_record_id "
                    "JOIN instrument i ON i.instrument_id=e.instrument_id "
                    "WHERE c.case_id=:case_id AND e.account_id=:account_id FOR UPDATE OF c, e"
                ), params).mappings().one_or_none()
                if case is None:
                    raise LookupError("Corporate-action case not found.")
                if case["instrument_id"] != case["event_instrument_id"] or not (
                    case["conid"] == case["instrument_conid"] == str(case["source_payload"].get("conid", ""))
                ):
                    raise ValueError("The case and broker security identities differ. Accounting support is required.")
                if case["reorg_code"] not in {"FORWARDSPLIT", "REVERSESPLIT", "STOCKDIV"} or not case["action_id"]:
                    raise ValueError("This action needs accounting support; no split correction is available.")
                if not case["requires_manual"]:
                    raise SplitCorrectionConflict("This action is already handled. Refresh the queue.")
                if (case["reorg_code"] == "REVERSESPLIT" and factor >= 1) or (
                    case["reorg_code"] != "REVERSESPLIT" and factor <= 1
                ):
                    raise ValueError("The ratio must match the split direction: new/old above 1 for a forward split, below 1 for a reverse split.")
                params.update(instrument_id=case["instrument_id"], start_date=case["report_date_local"])
                before = self._snapshots(connection, params)
                if not any(snapshot["report_date_local"] >= case["report_date_local"] for snapshot in before):
                    raise SplitCorrectionConflict("No affected snapshots are available. Ingest the broker statement before correcting this case.")
                newer_activity = connection.scalar(text(
                    "SELECT EXISTS (SELECT 1 FROM ("
                    "SELECT report_date_local FROM event_trade_fill WHERE account_id=:account_id AND instrument_id=:instrument_id "
                    "UNION ALL SELECT report_date_local FROM event_cashflow WHERE account_id=:account_id AND instrument_id=:instrument_id "
                    "UNION ALL SELECT report_date_local FROM event_corp_action WHERE account_id=:account_id AND instrument_id=:instrument_id"
                    ") activity WHERE report_date_local>:last_snapshot_date)"
                ), {**params, "last_snapshot_date": before[-1]["report_date_local"]})
                if newer_activity:
                    raise SplitCorrectionConflict("Newer canonical activity has no snapshot. Reprocess the failed ingestion before correcting this case.")
                accounting_inputs = self._accounting_inputs(connection, {
                    **params, "last_date": before[-1]["report_date_local"], "conid": case["conid"],
                    "run_ids": [str(snapshot["ingestion_run_id"]) for snapshot in before if snapshot["ingestion_run_id"] is not None],
                })
                lots_before = self._lots(connection, params)
                connection.execute(text(
                    "UPDATE corporate_action_manual_case SET split_factor=:factor, "
                    "resolution_source_raw_record_id=:source_id, resolution_report_date_local=:start_date, action_type=:action_type, status='resolved', resolution_note=:note, "
                    "resolved_at_utc=now(), updated_at_utc=now() WHERE case_id=:case_id"
                ), {**params, "factor": factor, "source_id": case["source_raw_record_id"], "action_type": case["reorg_code"], "note": note.strip()})
                connection.execute(text(
                    "UPDATE event_corp_action SET requires_manual=false, provisional=false "
                    "WHERE event_corp_action_id=:event_id"
                ), {"event_id": case["event_corp_action_id"]})
                ledger = StockLedgerSnapshotService(SQLAlchemyLedgerSnapshotService(self._engine, connection=connection))
                for snapshot in before:
                    if snapshot["report_date_local"] < case["report_date_local"]:
                        continue
                    ledger.ledger_snapshot_build_and_persist(
                        account_id=self._account_id,
                        ingestion_run_id=None if snapshot["ingestion_run_id"] is None else str(snapshot["ingestion_run_id"]),
                        report_date_local=str(snapshot["report_date_local"]),
                        functional_currency=snapshot["currency"],
                        affected_conids=frozenset({case["conid"]}),
                        affected_currencies=frozenset(),
                        reconcile_position_lots=snapshot["report_date_local"] == before[-1]["report_date_local"],
                    )
                # A manual case previously marked every date for this instrument
                # provisional. Clear that cause on earlier dates without changing
                # their pre-action quantities or P&L, retaining other uncertainty.
                connection.execute(text(
                    "UPDATE pnl_snapshot_daily SET provisional=calculation_provisional OR EXISTS "
                    "(SELECT 1 FROM corporate_action_manual_case c WHERE c.instrument_id=:instrument_id AND c.status='open') "
                    "OR EXISTS (SELECT 1 FROM event_corp_action e WHERE e.instrument_id=:instrument_id AND e.requires_manual) "
                    "WHERE account_id=:account_id AND instrument_id=:instrument_id AND report_date_local<:start_date"
                ), params)
                after = self._snapshots(connection, params)
                fields = ("position_qty", "cost_basis", "realized_pnl", "unrealized_pnl", "total_pnl", "provisional")
                result = {
                    "case_id": str(case_id), "symbol": case["symbol"], "factor": str(factor),
                    "report_date_local": str(case["report_date_local"]),
                    "snapshots": [
                        {"report_date_local": str(old["report_date_local"]), "currency": old["currency"],
                         "before": {key: old[key] for key in fields}, "after": {key: new[key] for key in fields}}
                        for old, new in zip(before, after, strict=True)
                    ],
                    "lots_before": lots_before, "lots_after": self._lots(connection, params),
                }
                result = json.loads(json.dumps(result, default=str))
                fingerprint = hashlib.sha256(json.dumps(
                    {"result": result, "before": before, "accounting_inputs": accounting_inputs, "source": case["source_payload"],
                     "case_updated": case["updated_at_utc"], "note": note.strip()},
                    default=str, sort_keys=True,
                ).encode()).hexdigest()
                if preview_token is not None and preview_token != fingerprint:
                    raise SplitCorrectionConflict("The data or ratio changed. Preview the correction again before applying it.")
                result["preview_token"] = fingerprint
                result["applied"] = preview_token is not None
                if preview_token is None:
                    transaction.rollback()
                return result

    def _lock_account(self, connection: Connection) -> None:
        # Use the ingestion start lock and check its durable active-run marker.
        # Holding this transaction lock prevents a new ingestion from starting.
        key_1, key_2 = SQLAlchemyIngestionRunService(self._engine)._build_advisory_lock_keys(self._account_id)
        locked = connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key_1, :key_2)"), {"key_1": key_1, "key_2": key_2})
        active = connection.scalar(text(
            "SELECT EXISTS(SELECT 1 FROM ingestion_run WHERE account_id=:account_id AND status='started')"
        ), {"account_id": self._account_id})
        if not locked or active:
            raise SplitCorrectionConflict("Ingestion or another correction is running. Try again after it finishes.")

    @staticmethod
    def _snapshots(connection: Connection, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [dict(row) for row in connection.execute(text(
            "SELECT report_date_local, ingestion_run_id, currency, position_qty, cost_basis, "
            "realized_pnl, unrealized_pnl, total_pnl, provisional FROM pnl_snapshot_daily "
            "WHERE account_id=:account_id AND instrument_id=:instrument_id "
            "ORDER BY report_date_local"
        ), params).mappings()]

    @staticmethod
    def _lots(connection: Connection, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [dict(row) for row in connection.execute(text(
            "SELECT l.open_event_trade_fill_id, l.remaining_quantity, l.open_price, l.cost_basis_open, "
            "l.cost_basis_open / (l.open_quantity * CASE WHEN t.side='SELL' THEN -1 ELSE 1 END) AS unit_basis "
            "FROM position_lot l JOIN event_trade_fill t ON t.event_trade_fill_id=l.open_event_trade_fill_id "
            "WHERE l.account_id=:account_id AND l.instrument_id=:instrument_id "
            "AND l.status='open' ORDER BY l.opened_at_utc, l.open_event_trade_fill_id"
        ), params).mappings()]


    @staticmethod
    def _accounting_inputs(connection: Connection, params: dict[str, Any]) -> dict[str, Any]:
        """Bind previews to source inputs, including closed execution identities."""
        # Keep JSON as database text so numeric inputs never round through floats.
        inputs: dict[str, Any] = {}
        for table in ("event_trade_fill", "event_cashflow", "event_corp_action", "event_fx"):
            manual_data = ", 'manual_case', to_jsonb(c)" if table == "event_corp_action" else ""
            manual_join = " LEFT JOIN corporate_action_manual_case c USING(event_corp_action_id)" if table == "event_corp_action" else ""
            instrument_scope = " AND e.instrument_id=:instrument_id" if table != "event_fx" else ""
            inputs[table] = connection.execute(text(
                f"SELECT (to_jsonb(e) || jsonb_build_object('source_payload', r.source_payload{manual_data}))::text "
                f"FROM {table} e JOIN raw_record r ON r.raw_record_id=e.source_raw_record_id{manual_join} "
                f"WHERE e.account_id=:account_id AND e.report_date_local<=:last_date{instrument_scope} "
                f"ORDER BY e.{table}_id"
            ), params).scalars().all()
        inputs["instrument"] = connection.scalar(text(
            "SELECT to_jsonb(i)::text FROM instrument i WHERE i.instrument_id=:instrument_id AND i.account_id=:account_id"
        ), params)
        inputs["valuations"] = connection.execute(text(
            "SELECT r.source_payload::text FROM raw_record r WHERE r.account_id=:account_id "
            "AND r.ingestion_run_id=ANY(CAST(:run_ids AS uuid[])) "
            "AND r.section_name='OpenPositions' AND r.source_payload->>'conid'=:conid ORDER BY r.raw_record_id"
        ), params).scalars().all()
        return inputs
