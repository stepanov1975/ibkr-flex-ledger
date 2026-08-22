"""Separate calculation uncertainty from manual-review provisional state."""

from alembic import op
import sqlalchemy as sa


revision = "20260822_06"
down_revision = "20260821_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pnl_snapshot_daily",
        sa.Column(
            "calculation_provisional",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        "UPDATE pnl_snapshot_daily "
        "SET calculation_provisional = provisional"
    )


def downgrade() -> None:
    op.drop_column("pnl_snapshot_daily", "calculation_provisional")
