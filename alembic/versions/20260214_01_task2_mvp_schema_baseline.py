"""Task 2 MVP schema baseline

Revision ID: 20260214_01
Revises: None
Create Date: 2026-02-14
"""
# pylint: disable=no-member,invalid-name,wrong-import-order

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260214_01"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "instrument",
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("conid", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("local_symbol", sa.Text(), nullable=True),
        sa.Column("isin", sa.Text(), nullable=True),
        sa.Column("cusip", sa.Text(), nullable=True),
        sa.Column("figi", sa.Text(), nullable=True),
        sa.Column("asset_category", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("account_id", "conid", name="uq_instrument_account_conid"),
    )
    op.create_index("ix_instrument_symbol", "instrument", ["symbol"])
    op.create_index("ix_instrument_updated_at_utc", "instrument", ["updated_at_utc"])

    op.create_table(
        "label",
        sa.Column("label_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("name", name="uq_label_name"),
    )

    op.create_table(
        "ingestion_run",
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("run_type", sa.Text(), nullable=False, server_default=sa.text("'scheduled'")),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("period_key", sa.Text(), nullable=False),
        sa.Column("flex_query_id", sa.Text(), nullable=False),
        sa.Column("report_date_local", sa.Date(), nullable=True),
        sa.Column("started_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("diagnostics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status in ('started', 'success', 'failed')", name="ck_ingestion_run_status"),
        sa.CheckConstraint("run_type in ('scheduled', 'manual', 'reprocess')", name="ck_ingestion_run_run_type"),
    )
    op.create_index(
        "ix_ingestion_run_started_ingestion_run",
        "ingestion_run",
        [sa.text("started_at_utc desc"), sa.text("ingestion_run_id desc")],
    )
    op.create_index("ix_ingestion_run_status_started", "ingestion_run", ["status", sa.text("started_at_utc desc")])

    op.create_table(
        "raw_record",
        sa.Column("raw_record_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("period_key", sa.Text(), nullable=False),
        sa.Column("flex_query_id", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("report_date_local", sa.Date(), nullable=True),
        sa.Column("section_name", sa.Text(), nullable=False),
        sa.Column("source_row_ref", sa.Text(), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_run.ingestion_run_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("ingestion_run_id", "section_name", "source_row_ref", name="uq_raw_record_section_source_ref"),
    )
    op.create_index("ix_raw_record_payload_dedupe", "raw_record", ["period_key", "flex_query_id", "payload_sha256"])
    op.create_index("ix_raw_record_section_name", "raw_record", ["section_name"])
    op.create_index("ix_raw_record_created_at_utc", "raw_record", ["created_at_utc"])

    op.create_table(
        "instrument_label",
        sa.Column(
            "instrument_label_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.instrument_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["label_id"], ["label.label_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("instrument_id", "label_id", name="uq_instrument_label_pair"),
    )
    op.create_index("ix_instrument_label_label_instrument", "instrument_label", ["label_id", "instrument_id"])

    op.create_table(
        "note",
        sa.Column("note_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("label_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("instrument_id is not null or label_id is not null", name="ck_note_target_required"),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.instrument_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["label_id"], ["label.label_id"], ondelete="SET NULL"),
    )
    op.create_index("ix_note_created_at_utc", "note", ["created_at_utc"])
    op.create_index("ix_note_instrument_created", "note", ["instrument_id", "created_at_utc"])
    op.create_index("ix_note_label_created", "note", ["label_id", "created_at_utc"])

    op.create_table(
        "event_trade_fill",
        sa.Column(
            "event_trade_fill_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_raw_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ib_exec_id", sa.Text(), nullable=False),
        sa.Column("transaction_id", sa.Text(), nullable=True),
        sa.Column("trade_timestamp_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_date_local", sa.Date(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("price", sa.Numeric(24, 8), nullable=False),
        sa.Column("cost", sa.Numeric(24, 8), nullable=True),
        sa.Column("commission", sa.Numeric(24, 8), nullable=True),
        sa.Column("fees", sa.Numeric(24, 8), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(24, 8), nullable=True),
        sa.Column("net_cash", sa.Numeric(24, 8), nullable=True),
        sa.Column("net_cash_in_base", sa.Numeric(24, 8), nullable=True),
        sa.Column("fx_rate_to_base", sa.Numeric(24, 10), nullable=True),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("functional_currency", sa.Text(), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("side in ('BUY', 'SELL')", name="ck_event_trade_fill_side"),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.instrument_id"]),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_run.ingestion_run_id"]),
        sa.ForeignKeyConstraint(["source_raw_record_id"], ["raw_record.raw_record_id"]),
        sa.UniqueConstraint("account_id", "ib_exec_id", name="uq_event_trade_fill_account_exec"),
    )
    op.create_index("ix_event_trade_fill_instrument_report_date", "event_trade_fill", ["instrument_id", "report_date_local"])
    op.create_index("ix_event_trade_fill_ingestion_run_id", "event_trade_fill", ["ingestion_run_id"])
    op.create_index("ix_event_trade_fill_source_raw_record_id", "event_trade_fill", ["source_raw_record_id"])

    op.create_table(
        "event_cashflow",
        sa.Column("event_cashflow_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_raw_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("cash_action", sa.Text(), nullable=False),
        sa.Column("report_date_local", sa.Date(), nullable=False),
        sa.Column("effective_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("amount_in_base", sa.Numeric(24, 8), nullable=True),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("functional_currency", sa.Text(), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("withholding_tax", sa.Numeric(24, 8), nullable=True),
        sa.Column("fees", sa.Numeric(24, 8), nullable=True),
        sa.Column("is_correction", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.instrument_id"]),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_run.ingestion_run_id"]),
        sa.ForeignKeyConstraint(["source_raw_record_id"], ["raw_record.raw_record_id"]),
        sa.UniqueConstraint(
            "account_id",
            "transaction_id",
            "cash_action",
            "currency",
            name="uq_event_cashflow_account_txn_action_ccy",
        ),
    )
    op.create_index("ix_event_cashflow_instrument_report_date", "event_cashflow", ["instrument_id", "report_date_local"])
    op.create_index("ix_event_cashflow_ingestion_run_id", "event_cashflow", ["ingestion_run_id"])
    op.create_index("ix_event_cashflow_source_raw_record_id", "event_cashflow", ["source_raw_record_id"])

    op.create_table(
        "event_fx",
        sa.Column("event_fx_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_raw_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("report_date_local", sa.Date(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("functional_currency", sa.Text(), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("fx_rate", sa.Numeric(24, 10), nullable=True),
        sa.Column("fx_source", sa.Text(), nullable=False),
        sa.Column("provisional", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("diagnostic_code", sa.Text(), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_run.ingestion_run_id"]),
        sa.ForeignKeyConstraint(["source_raw_record_id"], ["raw_record.raw_record_id"]),
        sa.UniqueConstraint(
            "account_id",
            "transaction_id",
            "currency",
            "functional_currency",
            name="uq_event_fx_account_txn_ccy_pair",
        ),
    )
    op.create_index("ix_event_fx_report_date_local", "event_fx", ["report_date_local"])
    op.create_index("ix_event_fx_ingestion_run_id", "event_fx", ["ingestion_run_id"])
    op.create_index("ix_event_fx_source_raw_record_id", "event_fx", ["source_raw_record_id"])

    op.create_table(
        "event_corp_action",
        sa.Column(
            "event_corp_action_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conid", sa.Text(), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_raw_record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", sa.Text(), nullable=True),
        sa.Column("transaction_id", sa.Text(), nullable=True),
        sa.Column("reorg_code", sa.Text(), nullable=False),
        sa.Column("report_date_local", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("requires_manual", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("provisional", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("manual_case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("action_id is not null or transaction_id is not null", name="ck_event_corp_action_key_present"),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.instrument_id"]),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_run.ingestion_run_id"]),
        sa.ForeignKeyConstraint(["source_raw_record_id"], ["raw_record.raw_record_id"]),
        sa.UniqueConstraint("account_id", "action_id", name="uq_event_corp_action_account_action"),
        sa.UniqueConstraint(
            "account_id",
            "transaction_id",
            "conid",
            "report_date_local",
            "reorg_code",
            name="uq_event_corp_action_fallback",
        ),
    )
    op.create_index("ix_event_corp_action_instrument_report_date", "event_corp_action", ["instrument_id", "report_date_local"])
    op.create_index("ix_event_corp_action_ingestion_run_id", "event_corp_action", ["ingestion_run_id"])
    op.create_index("ix_event_corp_action_source_raw_record_id", "event_corp_action", ["source_raw_record_id"])

    op.create_table(
        "position_lot",
        sa.Column("position_lot_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("open_event_trade_fill_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opened_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("open_quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("remaining_quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("open_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("cost_basis_open", sa.Numeric(24, 8), nullable=False),
        sa.Column("realized_pnl_to_date", sa.Numeric(24, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status in ('open', 'closed')", name="ck_position_lot_status"),
        sa.CheckConstraint("remaining_quantity >= 0", name="ck_position_lot_remaining_non_negative"),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.instrument_id"]),
        sa.ForeignKeyConstraint(["open_event_trade_fill_id"], ["event_trade_fill.event_trade_fill_id"]),
    )
    op.create_index("ix_position_lot_instrument_status", "position_lot", ["instrument_id", "status"])
    op.create_index("ix_position_lot_account_instrument", "position_lot", ["account_id", "instrument_id"])

    op.create_table(
        "pnl_snapshot_daily",
        sa.Column(
            "pnl_snapshot_daily_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("report_date_local", sa.Date(), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position_qty", sa.Numeric(24, 8), nullable=False),
        sa.Column("cost_basis", sa.Numeric(24, 8), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(24, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("unrealized_pnl", sa.Numeric(24, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("total_pnl", sa.Numeric(24, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("fees", sa.Numeric(24, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("withholding_tax", sa.Numeric(24, 8), nullable=False, server_default=sa.text("0")),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("provisional", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("valuation_source", sa.Text(), nullable=True),
        sa.Column("fx_source", sa.Text(), nullable=True),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.instrument_id"]),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_run.ingestion_run_id"]),
        sa.UniqueConstraint(
            "account_id",
            "report_date_local",
            "instrument_id",
            name="uq_pnl_snapshot_daily_account_date_instrument",
        ),
    )
    op.create_index("ix_pnl_snapshot_daily_report_date_instrument", "pnl_snapshot_daily", ["report_date_local", "instrument_id"])
    op.create_index("ix_pnl_snapshot_daily_provisional_report_date", "pnl_snapshot_daily", ["provisional", "report_date_local"])


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index("ix_pnl_snapshot_daily_provisional_report_date", table_name="pnl_snapshot_daily")
    op.drop_index("ix_pnl_snapshot_daily_report_date_instrument", table_name="pnl_snapshot_daily")
    op.drop_table("pnl_snapshot_daily")

    op.drop_index("ix_position_lot_account_instrument", table_name="position_lot")
    op.drop_index("ix_position_lot_instrument_status", table_name="position_lot")
    op.drop_table("position_lot")

    op.drop_index("ix_event_corp_action_source_raw_record_id", table_name="event_corp_action")
    op.drop_index("ix_event_corp_action_ingestion_run_id", table_name="event_corp_action")
    op.drop_index("ix_event_corp_action_instrument_report_date", table_name="event_corp_action")
    op.drop_table("event_corp_action")

    op.drop_index("ix_event_fx_source_raw_record_id", table_name="event_fx")
    op.drop_index("ix_event_fx_ingestion_run_id", table_name="event_fx")
    op.drop_index("ix_event_fx_report_date_local", table_name="event_fx")
    op.drop_table("event_fx")

    op.drop_index("ix_event_cashflow_source_raw_record_id", table_name="event_cashflow")
    op.drop_index("ix_event_cashflow_ingestion_run_id", table_name="event_cashflow")
    op.drop_index("ix_event_cashflow_instrument_report_date", table_name="event_cashflow")
    op.drop_table("event_cashflow")

    op.drop_index("ix_event_trade_fill_source_raw_record_id", table_name="event_trade_fill")
    op.drop_index("ix_event_trade_fill_ingestion_run_id", table_name="event_trade_fill")
    op.drop_index("ix_event_trade_fill_instrument_report_date", table_name="event_trade_fill")
    op.drop_table("event_trade_fill")

    op.drop_index("ix_note_label_created", table_name="note")
    op.drop_index("ix_note_instrument_created", table_name="note")
    op.drop_index("ix_note_created_at_utc", table_name="note")
    op.drop_table("note")

    op.drop_index("ix_instrument_label_label_instrument", table_name="instrument_label")
    op.drop_table("instrument_label")

    op.drop_index("ix_raw_record_created_at_utc", table_name="raw_record")
    op.drop_index("ix_raw_record_section_name", table_name="raw_record")
    op.drop_index("ix_raw_record_payload_dedupe", table_name="raw_record")
    op.drop_table("raw_record")

    op.drop_index("ix_ingestion_run_status_started", table_name="ingestion_run")
    op.drop_index("ix_ingestion_run_started_ingestion_run", table_name="ingestion_run")
    op.drop_table("ingestion_run")

    op.drop_table("label")

    op.drop_index("ix_instrument_updated_at_utc", table_name="instrument")
    op.drop_index("ix_instrument_symbol", table_name="instrument")
    op.drop_table("instrument")
