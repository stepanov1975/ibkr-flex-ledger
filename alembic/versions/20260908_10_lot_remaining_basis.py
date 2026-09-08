"""Persist remaining lot basis independently of historical opening basis."""

from alembic import op
import sqlalchemy as sa

revision = "20260908_10"
down_revision = "20260908_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("position_lot", sa.Column("cost_basis_remaining", sa.Numeric(24, 8), nullable=True))
    op.execute("UPDATE position_lot SET cost_basis_remaining=CASE WHEN remaining_quantity=0 THEN 0 "
               "ELSE cost_basis_open * remaining_quantity / open_quantity END")
    op.alter_column("position_lot", "cost_basis_remaining", nullable=False)


def downgrade() -> None:
    op.drop_column("position_lot", "cost_basis_remaining")
