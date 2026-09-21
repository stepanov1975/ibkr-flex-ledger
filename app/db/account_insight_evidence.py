"""Canonical and FIFO evidence for independent read-only account checks."""

from typing import Any
from datetime import date
from sqlalchemy import Connection, text
from .ledger_snapshot import SQLAlchemyLedgerSnapshotService


def db_account_insight_evidence(connection: Connection, account_id: str) -> dict[str, Any]:
    """Read ledger projections and their source metadata in the caller's transaction."""
    params = {"account_id": account_id}
    instruments = [dict(row) for row in connection.execute(text(
        "SELECT instrument_id::text,conid,symbol,asset_category FROM instrument WHERE account_id=:account_id"
    ), params).mappings()]
    snapshots = [dict(row) for row in connection.execute(text(
        "SELECT instrument_id::text,report_date_local,currency,realized_pnl,unrealized_pnl,cost_basis,"
        "provisional,calculated_at_utc,fx_dependencies FROM pnl_snapshot_daily WHERE account_id=:account_id"
    ), params).mappings()]
    lots = [dict(row) for row in connection.execute(text(
        "SELECT l.instrument_id::text,l.remaining_quantity,l.cost_basis_remaining,COALESCE(t.side,'BUY') AS side "
        "FROM position_lot l LEFT JOIN event_trade_fill t ON t.event_trade_fill_id=l.open_event_trade_fill_id "
        "WHERE l.account_id=:account_id"
    ), params).mappings()]
    trades = [dict(row) for row in connection.execute(text(
        "SELECT e.event_trade_fill_id::text AS event_id,e.instrument_id::text,e.report_date_local,"
        "e.trade_timestamp_utc,e.side,e.quantity,e.price,e.currency,e.net_cash,e.commission,e.fees,"
        "e.updated_at_utc,r.source_payload AS payload,e.source_raw_record_id::text AS raw_id "
        "FROM event_trade_fill e JOIN raw_record r ON r.raw_record_id="
        "COALESCE(e.metadata_source_raw_record_id,e.source_raw_record_id) WHERE e.account_id=:account_id"
    ), params).mappings()]
    cash = [dict(row) for row in connection.execute(text(
        "SELECT e.event_cashflow_id::text AS event_id,e.instrument_id::text,e.report_date_local,"
        "e.cash_action,e.amount,e.amount_in_base,e.currency,e.fees,e.withholding_tax,e.updated_at_utc,"
        "r.source_payload AS payload,e.source_raw_record_id::text AS raw_id "
        "FROM event_cashflow e JOIN raw_record r ON r.raw_record_id=e.source_raw_record_id "
        "WHERE e.account_id=:account_id"
    ), params).mappings()]
    # Account-wide invalidation is conservative for corrections with cross-instrument effects.
    mutation = connection.execute(text(
        "SELECT max(changed) FROM ("
        "SELECT max(updated_at_utc) AS changed FROM event_corp_action WHERE account_id=:account_id UNION ALL "
        "SELECT max(cashflow_reassigned_at_utc) FROM instrument WHERE account_id=:account_id) mutations"
    ), params).scalar_one()
    fx = SQLAlchemyLedgerSnapshotService(connection.engine, connection=connection).db_ledger_fx_rate_list_for_account(
        account_id, max((s["report_date_local"] for s in snapshots), default=date.today()).isoformat(),
    )
    return {"fx": fx, "instruments": instruments, "snapshots": snapshots, "lots": lots, "trades": trades,
            "cash": cash, "mutation": mutation}
