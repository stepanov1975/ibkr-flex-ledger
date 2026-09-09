"""Keep an applied split factor tied to the immutable source it corrects."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260908_08"
down_revision = "20260822_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("corporate_action_manual_case", sa.Column("split_factor", sa.Numeric(), nullable=True))
    op.add_column("corporate_action_manual_case", sa.Column(
        "resolution_source_raw_record_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("raw_record.raw_record_id"), nullable=True,
    ))
    op.create_check_constraint(
        "ck_manual_case_split_factor", "corporate_action_manual_case",
        "split_factor IS NULL OR (split_factor > 0 AND split_factor NOT IN "
        "('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric) AND resolution_source_raw_record_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_manual_case_split_factor", "corporate_action_manual_case")
    op.drop_column("corporate_action_manual_case", "resolution_source_raw_record_id")
    op.drop_column("corporate_action_manual_case", "split_factor")
