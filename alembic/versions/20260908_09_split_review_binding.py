"""Bind split approvals to their date and repair incompatible legacy actions."""

from alembic import op
import sqlalchemy as sa

revision = "20260908_09"
down_revision = "20260908_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("corporate_action_manual_case", sa.Column("resolution_report_date_local", sa.Date(), nullable=True))
    # Keep the data repair in SQL so offline migration scripts perform the same
    # repair as an online upgrade. Helpers exist only for this migration.
    op.execute("""
CREATE FUNCTION pg_temp.split_review_decimal(value text) RETURNS numeric
LANGUAGE plpgsql AS $function$
DECLARE number numeric;
BEGIN
    number := replace(value, '_', '')::numeric;
    IF number IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric) THEN RETURN NULL; END IF;
    RETURN number;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN NULL;
END $function$;
CREATE FUNCTION pg_temp.split_review_date(value text, fallback date) RETURNS date
LANGUAGE plpgsql SET datestyle='ISO, MDY' AS $function$
DECLARE candidate text;
BEGIN
    IF value IS NULL OR upper(btrim(value)) IN ('', '-', '--', 'N/A') THEN RETURN fallback; END IF;
    FOREACH candidate IN ARRAY ARRAY[btrim(value), split_part(btrim(value), ';', 1),
        split_part(btrim(value), 'T', 1), split_part(btrim(value), ' ', 1)] LOOP
        BEGIN
            -- Match strptime's two-digit year cutoff (PostgreSQL uses 2069).
            IF candidate ~ '^([0-9]{1,2}-[A-Za-z]{3}-|[0-9]{1,2}/[0-9]{1,2}/)69($|[; T])' THEN
                candidate := regexp_replace(candidate, '69($|[; T])', '1969\\1');
            END IF;
            RETURN candidate::date;
        EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN CONTINUE;
        END;
    END LOOP;
    RETURN NULL;
END $function$;
-- Only the direction relative to one is needed. These exact thresholds
-- preserve Decimal's 28-significant-digit, half-even division around one.
CREATE FUNCTION pg_temp.split_review_ratio(new_quantity numeric, old_quantity numeric) RETURNS numeric
LANGUAGE SQL AS $function$
SELECT CASE WHEN new_quantity<=0 THEN 0
    WHEN new_quantity>old_quantity*(1+5e-28::numeric) THEN 2
    WHEN new_quantity<old_quantity*(1-5e-29::numeric) THEN 0.5 ELSE 1 END
$function$;
DO $repair$
DECLARE
    action record;
    approved_date date;
    manual_bound boolean;
    factor numeric;
    new_quantity numeric;
    old_quantity numeric;
    clauses text[][];
BEGIN
    FOR action IN
        SELECT e.*, c.case_id, c.split_factor, c.instrument_id AS case_instrument_id,
               c.action_type AS case_action_type, r.source_payload,
               original.source_payload AS approved_payload, original.report_date_local AS approved_artifact_date
        FROM event_corp_action e JOIN raw_record r ON r.raw_record_id=e.source_raw_record_id
        LEFT JOIN corporate_action_manual_case c USING(event_corp_action_id)
        LEFT JOIN raw_record original ON original.raw_record_id=c.resolution_source_raw_record_id
        WHERE e.reorg_code IN ('FORWARDSPLIT','REVERSESPLIT','STOCKDIV') OR c.split_factor IS NOT NULL
    LOOP
        approved_date := NULL;
        manual_bound := false;
        IF action.split_factor IS NOT NULL THEN
            approved_date := pg_temp.split_review_date(action.approved_payload->>'reportDate', action.approved_artifact_date);
            UPDATE corporate_action_manual_case SET resolution_report_date_local=approved_date WHERE case_id=action.case_id;
            manual_bound := COALESCE(
                action.approved_payload=action.source_payload AND approved_date=action.report_date_local
                AND action.case_instrument_id=action.instrument_id AND action.case_action_type=action.reorg_code
                AND action.approved_payload->>'conid'=action.conid AND action.action_id IS NOT NULL, false);
        ELSIF action.requires_manual THEN
            -- An unchanged, already-manual case needs no compatibility repair.
            CONTINUE;
        END IF;
        IF manual_bound THEN CONTINUE; END IF;
        factor := pg_temp.split_review_decimal(action.source_payload->>'ratio');
        IF factor IS NULL THEN
            new_quantity := pg_temp.split_review_decimal(action.source_payload->>'newQuantity');
            old_quantity := pg_temp.split_review_decimal(action.source_payload->>'oldQuantity');
            IF new_quantity IS NOT NULL AND old_quantity>0 THEN
                factor := pg_temp.split_review_ratio(new_quantity, old_quantity);
            ELSIF COALESCE(btrim(action.source_payload->>'ratio'), '')=''
                AND COALESCE(btrim(action.source_payload->>'newQuantity'), '')=''
                AND COALESCE(btrim(action.source_payload->>'oldQuantity'), '')='' THEN
                SELECT array_agg(parts) INTO clauses FROM regexp_matches(
                    upper(COALESCE(action.source_payload->>'description', '')),
                    '\\mSPLIT\\s+([0-9]+(?:\\.[0-9]+)?)\\s+FOR\\s+([0-9]+(?:\\.[0-9]+)?)\\s*\\(', 'g') AS parts;
                IF array_length(clauses, 1)=1 THEN
                    new_quantity := pg_temp.split_review_decimal(clauses[1][1]);
                    old_quantity := pg_temp.split_review_decimal(clauses[1][2]);
                    IF new_quantity>0 AND old_quantity>0 THEN factor := pg_temp.split_review_ratio(new_quantity, old_quantity); END IF;
                END IF;
            END IF;
        END IF;
        IF (action.reorg_code='REVERSESPLIT' AND factor>0 AND factor<1)
            OR (action.reorg_code IN ('FORWARDSPLIT','STOCKDIV') AND factor>1) THEN CONTINUE; END IF;
        UPDATE event_corp_action SET requires_manual=true, provisional=true
        WHERE event_corp_action_id=action.event_corp_action_id;
        IF action.instrument_id IS NOT NULL THEN
            INSERT INTO corporate_action_manual_case (event_corp_action_id, action_type, instrument_id)
            VALUES (action.event_corp_action_id, action.reorg_code, action.instrument_id)
            ON CONFLICT (event_corp_action_id) DO UPDATE SET status='open', resolved_at_utc=NULL, updated_at_utc=now();
            UPDATE pnl_snapshot_daily SET calculation_provisional=true, provisional=true
            WHERE account_id=action.account_id AND instrument_id=action.instrument_id
                AND report_date_local>=LEAST(action.report_date_local, COALESCE(approved_date, action.report_date_local));
        END IF;
    END LOOP;
END $repair$;
DROP FUNCTION pg_temp.split_review_decimal(text);
DROP FUNCTION pg_temp.split_review_ratio(numeric, numeric);
DROP FUNCTION pg_temp.split_review_date(text, date);
""")


def downgrade() -> None:
    op.drop_column("corporate_action_manual_case", "resolution_report_date_local")
