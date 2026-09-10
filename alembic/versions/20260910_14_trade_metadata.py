"""Retain corrected trade descriptions without invalidating calculated P&L."""

from alembic import op
import sqlalchemy as sa

revision = "20260910_14"
down_revision = "20260910_13"
branch_labels = None
depends_on = None


def _mutation_function(trade_metadata: bool) -> None:
    trade_comparison = """
            IF TG_TABLE_NAME = 'event_trade_fill' THEN
                IF (to_jsonb(NEW) - ARRAY['cost','realized_pnl','description','updated_at_utc'])
                    IS NOT DISTINCT FROM
                    (to_jsonb(OLD) - ARRAY['cost','realized_pnl','description','updated_at_utc']) THEN
                    RETURN NEW;
                END IF;
            END IF;
    """ if trade_metadata else ""
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
    """ + trade_comparison + """
            IF NEW IS DISTINCT FROM OLD THEN
                NEW.updated_at_utc = clock_timestamp();
            END IF;
            RETURN NEW;
        END;
        $$
    """)


def upgrade() -> None:
    op.add_column('event_trade_fill', sa.Column('description', sa.Text(), nullable=True))
    _mutation_function(trade_metadata=True)
    op.execute("""
        UPDATE event_trade_fill event SET description=NULLIF(BTRIM(raw.source_payload->>'description'), '')
        FROM raw_record raw WHERE raw.raw_record_id=event.source_raw_record_id
    """)


def downgrade() -> None:
    op.drop_column('event_trade_fill', 'description')
    _mutation_function(trade_metadata=False)
