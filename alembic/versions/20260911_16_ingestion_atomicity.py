"""Distinguish atomic workflows from legacy runs with possible partial writes."""
from alembic import op
import sqlalchemy as sa

revision = '20260911_16'
down_revision = '20260910_15'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ingestion_run', sa.Column('semantic_atomic', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('ingestion_run', 'semantic_atomic')
