"""Track canonical mutations and actual P&L calculation times."""

from alembic import op
import sqlalchemy as sa

revision = "20260909_11"
down_revision = "20260908_10"
branch_labels = None
depends_on = None

_EVENT_TABLES = ("event_trade_fill", "event_cashflow", "event_corp_action")


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION track_canonical_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW IS DISTINCT FROM OLD THEN
                NEW.updated_at_utc = clock_timestamp();
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    for table in _EVENT_TABLES:
        op.add_column(table, sa.Column("updated_at_utc", sa.DateTime(timezone=True),
                                      nullable=False, server_default=sa.text("clock_timestamp()")))
        op.execute(f"CREATE TRIGGER canonical_mutation BEFORE UPDATE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION track_canonical_mutation()")
    # Existing snapshots have unknown freshness: earlier corrections did not retain
    # mutation/calculation times. A rebuild establishes a reliable timestamp.
    op.add_column("pnl_snapshot_daily", sa.Column("calculated_at_utc", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("pnl_snapshot_daily", "calculated_at_utc", server_default=sa.text("clock_timestamp()"))


def downgrade() -> None:
    op.drop_column("pnl_snapshot_daily", "calculated_at_utc")
    for table in reversed(_EVENT_TABLES):
        op.execute(f"DROP TRIGGER canonical_mutation ON {table}")
        op.drop_column(table, "updated_at_utc")
    op.execute("DROP FUNCTION track_canonical_mutation()")
