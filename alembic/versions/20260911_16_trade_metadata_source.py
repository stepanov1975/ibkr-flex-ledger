"""Keep refreshed trade metadata separate from its immutable audit origin."""

from alembic import op
import sqlalchemy as sa

revision = "20260911_16"
down_revision = "20260910_15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "event_trade_fill",
        sa.Column("metadata_source_raw_record_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_trade_metadata_source", "event_trade_fill", "raw_record",
        ["metadata_source_raw_record_id"], ["raw_record_id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_trade_metadata_source", "event_trade_fill", type_="foreignkey")
    op.drop_column("event_trade_fill", "metadata_source_raw_record_id")
