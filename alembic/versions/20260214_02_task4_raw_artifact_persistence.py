"""Task 4 raw artifact persistence baseline

Revision ID: 20260214_02
Revises: 20260214_01
Create Date: 2026-02-14
"""
# pylint: disable=no-member,invalid-name,wrong-import-order

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260214_02"
down_revision: Union[str, Sequence[str], None] = "20260214_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "raw_artifact",
        sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("period_key", sa.Text(), nullable=False),
        sa.Column("flex_query_id", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("report_date_local", sa.Date(), nullable=True),
        sa.Column("source_payload", postgresql.BYTEA(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_run.ingestion_run_id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "account_id",
            "period_key",
            "flex_query_id",
            "payload_sha256",
            name="uq_raw_artifact_account_period_query_sha256",
        ),
    )
    op.create_index("ix_raw_artifact_created_at_utc", "raw_artifact", ["created_at_utc"])
    op.create_index("ix_raw_artifact_ingestion_run_id", "raw_artifact", ["ingestion_run_id"])

    op.add_column("raw_record", sa.Column("raw_artifact_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_raw_record_raw_artifact",
        "raw_record",
        "raw_artifact",
        ["raw_artifact_id"],
        ["raw_artifact_id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("uq_raw_record_section_source_ref", "raw_record", type_="unique")
    op.create_unique_constraint(
        "uq_raw_record_artifact_section_source_ref",
        "raw_record",
        ["raw_artifact_id", "section_name", "source_row_ref"],
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_constraint("uq_raw_record_artifact_section_source_ref", "raw_record", type_="unique")
    op.create_unique_constraint(
        "uq_raw_record_section_source_ref",
        "raw_record",
        ["ingestion_run_id", "section_name", "source_row_ref"],
    )
    op.drop_constraint("fk_raw_record_raw_artifact", "raw_record", type_="foreignkey")
    op.drop_column("raw_record", "raw_artifact_id")

    op.drop_index("ix_raw_artifact_ingestion_run_id", table_name="raw_artifact")
    op.drop_index("ix_raw_artifact_created_at_utc", table_name="raw_artifact")
    op.drop_table("raw_artifact")
