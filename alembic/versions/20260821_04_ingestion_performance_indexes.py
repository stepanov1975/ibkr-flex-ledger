"""Add raw-record indexes for incremental ingestion."""

from alembic import op


revision = "20260821_04"
down_revision = "20260821_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_raw_record_run_created_id "
        "ON raw_record (ingestion_run_id, created_at_utc, raw_record_id)"
    )
    op.execute(
        "CREATE INDEX ix_raw_record_prior_version ON raw_record "
        "(account_id, flex_query_id, section_name, source_row_ref, "
        "created_at_utc DESC, raw_record_id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_raw_record_prior_version")
    op.execute("DROP INDEX ix_raw_record_run_created_id")
