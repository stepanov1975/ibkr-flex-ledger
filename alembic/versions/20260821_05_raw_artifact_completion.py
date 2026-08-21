"""Add durable raw-artifact processing completion lineage."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260821_05"
down_revision = "20260821_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_artifact",
        sa.Column(
            "completed_ingestion_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_raw_artifact_completed_ingestion_run",
        "raw_artifact",
        "ingestion_run",
        ["completed_ingestion_run_id"],
        ["ingestion_run_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_raw_artifact_completed_ingestion_run",
        "raw_artifact",
        type_="foreignkey",
    )
    op.drop_column("raw_artifact", "completed_ingestion_run_id")
