"""Track FX mutations and repeated broker valuation attempts."""

from alembic import op
import sqlalchemy as sa

revision = "20260910_12"
down_revision = "20260909_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("event_fx", sa.Column("updated_at_utc", sa.DateTime(timezone=True),
                                      nullable=False, server_default=sa.text("clock_timestamp()")))
    op.execute("CREATE TRIGGER canonical_mutation BEFORE UPDATE ON event_fx "
               "FOR EACH ROW EXECUTE FUNCTION track_canonical_mutation()")
    op.add_column("raw_artifact", sa.Column("valuation_pending_at_utc", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("raw_artifact", "valuation_pending_at_utc")
    op.execute("DROP TRIGGER canonical_mutation ON event_fx")
    op.drop_column("event_fx", "updated_at_utc")
