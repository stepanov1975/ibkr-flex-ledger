"""Track former cashflow owners and ignore unconsumed local amounts."""

from alembic import op
import sqlalchemy as sa

revision = "20260910_15"
down_revision = "20260910_14"
branch_labels = None
depends_on = None


def _mutation_function(track_cashflows: bool) -> None:
    reassignment = """
                IF OLD.instrument_id IS DISTINCT FROM NEW.instrument_id THEN
                    UPDATE instrument SET cashflow_reassigned_at_utc=clock_timestamp()
                    WHERE account_id=OLD.account_id AND instrument_id=OLD.instrument_id;
                END IF;
    """ if track_cashflows else ""
    ignored_amount = ",'amount'" if track_cashflows else ""
    amount_comparison = """
                    AND (NEW.amount IS NOT DISTINCT FROM OLD.amount
                        OR (NEW.amount_in_base IS NOT NULL AND UPPER(BTRIM(NEW.functional_currency)) =
                            COALESCE((SELECT UPPER(BTRIM(currency)) FROM pnl_snapshot_daily
                                WHERE account_id=NEW.account_id AND instrument_id=NEW.instrument_id
                                ORDER BY report_date_local DESC LIMIT 1), UPPER(BTRIM(NEW.functional_currency)))))
    """ if track_cashflows else ""
    op.execute(f"""
        CREATE OR REPLACE FUNCTION track_canonical_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'event_cashflow' THEN
                {reassignment}
                IF (to_jsonb(NEW) - ARRAY['ingestion_run_id','source_raw_record_id','effective_at_utc',
                    'created_at_utc','updated_at_utc','cash_action','is_correction','transaction_id'{ignored_amount}])
                    IS NOT DISTINCT FROM
                    (to_jsonb(OLD) - ARRAY['ingestion_run_id','source_raw_record_id','effective_at_utc',
                    'created_at_utc','updated_at_utc','cash_action','is_correction','transaction_id'{ignored_amount}])
                    {amount_comparison} THEN
                    RETURN NEW;
                END IF;
            END IF;
            IF TG_TABLE_NAME = 'event_trade_fill' THEN
                IF (to_jsonb(NEW) - ARRAY['cost','realized_pnl','description','updated_at_utc','net_cash'])
                    IS NOT DISTINCT FROM
                    (to_jsonb(OLD) - ARRAY['cost','realized_pnl','description','updated_at_utc','net_cash'])
                    AND (UPPER(BTRIM(NEW.currency)) = UPPER(BTRIM(NEW.functional_currency))
                        OR NEW.fx_rate_to_base > 0
                        OR (NULLIF(ABS(NEW.net_cash_in_base), 0) / NULLIF(ABS(NEW.net_cash), 0))
                            IS NOT DISTINCT FROM
                            (NULLIF(ABS(OLD.net_cash_in_base), 0) / NULLIF(ABS(OLD.net_cash), 0))) THEN
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


def upgrade() -> None:
    op.add_column('instrument', sa.Column('cashflow_reassigned_at_utc', sa.DateTime(timezone=True), nullable=True))
    _mutation_function(track_cashflows=True)


def downgrade() -> None:
    _mutation_function(track_cashflows=False)
    op.drop_column('instrument', 'cashflow_reassigned_at_utc')
