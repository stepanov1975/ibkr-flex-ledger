"""Add corporate-action manual case workflow.

Revision ID: 20260821_03
Revises: 20260214_02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260821_03"
down_revision: Union[str, Sequence[str], None] = "20260214_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the durable corporate-action manual case table."""

    op.create_table(
        "corporate_action_manual_case",
        sa.Column("case_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_corp_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("owner", sa.Text(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status in ('open', 'resolved', 'dismissed')", name="ck_corporate_action_manual_case_status"),
        sa.ForeignKeyConstraint(["event_corp_action_id"], ["event_corp_action.event_corp_action_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["instrument_id"], ["instrument.instrument_id"]),
        sa.UniqueConstraint("event_corp_action_id", name="uq_corporate_action_manual_case_event"),
    )
    op.create_index(
        "ix_corporate_action_manual_case_status_instrument",
        "corporate_action_manual_case",
        ["status", "instrument_id"],
    )
    op.create_foreign_key(
        "fk_event_corp_action_manual_case",
        "event_corp_action",
        "corporate_action_manual_case",
        ["manual_case_id"],
        ["case_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Remove corporate-action manual case storage."""

    op.drop_constraint("fk_event_corp_action_manual_case", "event_corp_action", type_="foreignkey")
    op.drop_index("ix_corporate_action_manual_case_status_instrument", table_name="corporate_action_manual_case")
    op.drop_table("corporate_action_manual_case")
