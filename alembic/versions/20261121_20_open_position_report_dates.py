"""Mark snapshots whose linked position evidence has a different business date."""

from datetime import date, datetime

from alembic import op
from sqlalchemy import text

revision = "20261121_20"
down_revision = "20261117_19"
branch_labels = None
depends_on = None


def _matches_statement_day(raw_date: str, statement_day: date) -> bool:
    """Interpret stored Flex date forms using this revision's date contract."""
    value = raw_date.strip()
    if value in ("", "-", "--", "N/A"):
        return True
    candidates = (value, *(value.split(separator, 1)[0] for separator in (";", "T", " ")))
    for candidate in dict.fromkeys(candidates):
        try:
            return date.fromisoformat(candidate) == statement_day
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(candidate).date() == statement_day
        except ValueError:
            pass
        for date_format in ("%Y/%m/%d", "%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%y"):
            try:
                return datetime.strptime(candidate, date_format).date() == statement_day
            except ValueError:
                continue
    return False


def upgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("OpenPositions date validation requires an online migration")
    connection = op.get_bind()
    candidates = connection.execute(text("""
        SELECT DISTINCT snapshot.pnl_snapshot_daily_id, snapshot.report_date_local,
                        raw.source_payload->>'reportDate' AS position_report_date
        FROM pnl_snapshot_daily snapshot
        JOIN instrument ON instrument.instrument_id=snapshot.instrument_id AND instrument.account_id=snapshot.account_id
        JOIN raw_record raw ON raw.account_id=snapshot.account_id AND raw.ingestion_run_id=snapshot.ingestion_run_id
        AND raw.report_date_local=snapshot.report_date_local AND raw.source_payload->>'conid'=instrument.conid
        WHERE raw.section_name='OpenPositions' AND raw.source_row_ref LIKE 'OpenPositions:OpenPosition:%'
        AND BTRIM(raw.source_payload->>'reportDate') NOT IN (
            '', '-', '--', 'N/A', to_char(snapshot.report_date_local, 'YYYYMMDD'),
            to_char(snapshot.report_date_local, 'YYYY-MM-DD')
        )
        AND COALESCE(NULLIF(UPPER(BTRIM(raw.source_payload->>'levelOfDetail')), ''), 'SUMMARY')='SUMMARY'
        AND UPPER(BTRIM(instrument.asset_category)) NOT IN ('CASH', 'FX')
    """))
    affected_ids = {
        row.pnl_snapshot_daily_id for row in candidates
        if not _matches_statement_day(row.position_report_date, row.report_date_local)
    }
    if affected_ids:
        connection.execute(text("""
            UPDATE pnl_snapshot_daily SET calculation_provisional=true, provisional=true,
                valuation_source='openpositions_summary_unavailable'
            WHERE pnl_snapshot_daily_id=ANY(CAST(:ids AS uuid[]))
        """), {"ids": list(affected_ids)})


def downgrade() -> None:
    """Old valuations cannot be confirmed without replaying their evidence."""
