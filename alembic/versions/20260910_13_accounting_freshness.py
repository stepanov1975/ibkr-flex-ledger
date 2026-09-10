"""Track consumed FX rates and ignore non-accounting cashflow changes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260910_13"
down_revision = "20260910_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('pnl_snapshot_daily', sa.Column('fx_dependencies', postgresql.JSONB(), nullable=True))
    # Effective FX dependency values replace the coarse FX mutation timestamp.
    op.execute('DROP TRIGGER canonical_mutation ON event_fx')
    op.drop_column('event_fx', 'updated_at_utc')
    op.execute("""
        CREATE OR REPLACE FUNCTION track_canonical_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'event_cashflow' THEN
                IF (to_jsonb(NEW) - ARRAY['ingestion_run_id','source_raw_record_id','effective_at_utc',
                    'created_at_utc','updated_at_utc','cash_action','is_correction','transaction_id'])
                    IS NOT DISTINCT FROM
                    (to_jsonb(OLD) - ARRAY['ingestion_run_id','source_raw_record_id','effective_at_utc',
                    'created_at_utc','updated_at_utc','cash_action','is_correction','transaction_id']) THEN
                    RETURN NEW;
                END IF;
            END IF;
            IF NEW IS DISTINCT FROM OLD THEN
                NEW.updated_at_utc = clock_timestamp();
            END IF;
            RETURN NEW;
        END;
        $$
    """)


def downgrade() -> None:
    op.drop_column('pnl_snapshot_daily', 'fx_dependencies')
    op.add_column('event_fx', sa.Column('updated_at_utc', sa.DateTime(timezone=True),
                                      nullable=False, server_default=sa.text('clock_timestamp()')))
    op.execute('CREATE TRIGGER canonical_mutation BEFORE UPDATE ON event_fx '
               'FOR EACH ROW EXECUTE FUNCTION track_canonical_mutation()')
    op.execute("""
        CREATE OR REPLACE FUNCTION track_canonical_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW IS DISTINCT FROM OLD THEN
                NEW.updated_at_utc = clock_timestamp();
            END IF;
            RETURN NEW;
        END;
        $$
    """)
