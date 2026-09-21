"""Successful Flex section selection for read-only account insights."""

from sqlalchemy import Engine, text
from typing import Any

from app.analytics.account_holdings import account_lending, account_settled_cash
from app.analytics.account_income import account_income
from app.analytics.account_checks import account_calculation_checks
from .account_insight_evidence import db_account_insight_evidence
from app.analytics.account_insights import AccountInsights, InsightRow

_SECTIONS = ("EquitySummaryInBase", "CashReport", "OpenPositions", "ChangeInNAV",
             "FIFOPerformanceSummaryInBase", "MTMPerformanceSummaryInBase",
             "OpenDividendAccruals", "ChangeInDividendAccruals", "InterestAccruals", "NetStockPositionSummary")
_ELIGIBLE = """
SELECT a.* FROM raw_artifact a
JOIN ingestion_run owner ON owner.ingestion_run_id=a.ingestion_run_id
LEFT JOIN ingestion_run completion ON completion.ingestion_run_id=a.completed_ingestion_run_id
WHERE a.account_id=:account_id AND a.report_date_local IS NOT NULL
AND (completion.status='success' OR (a.completed_ingestion_run_id IS NULL AND owner.status='success'))
"""


def db_account_insights(engine: Engine, account_id: str) -> dict[str, Any]:
    """Read coherent successful sources; failed reports never replace published data."""
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        rows = connection.execute(text("""
            WITH eligible AS (""" + _ELIGIBLE + """), versions AS (
              SELECT DISTINCT ON (r.section_name) r.section_name,a.raw_artifact_id
              FROM eligible a JOIN raw_record r USING(raw_artifact_id)
              WHERE r.section_name=ANY(:sections)
              ORDER BY r.section_name,a.report_date_local DESC,a.created_at_utc DESC,a.raw_artifact_id DESC)
            SELECT r.section_name AS section,r.source_payload AS payload,r.raw_record_id::text AS raw_id,
                   a.raw_artifact_id::text AS artifact_id,a.report_date_local AS report_date
            FROM versions v JOIN eligible a USING(raw_artifact_id)
            JOIN raw_record r ON r.raw_artifact_id=v.raw_artifact_id AND r.section_name=v.section_name
            ORDER BY r.section_name,r.source_row_ref,r.raw_record_id
        """), {"account_id": account_id, "sections": list(_SECTIONS)}).mappings().all()
        history = connection.execute(text("""
            WITH eligible AS (""" + _ELIGIBLE + """)
            SELECT
                r.section_name AS section,r.source_payload AS payload,r.raw_record_id::text AS raw_id,
                a.raw_artifact_id::text AS artifact_id,a.report_date_local AS report_date
            FROM eligible a JOIN raw_record r USING(raw_artifact_id)
            WHERE r.section_name IN ('EquitySummaryInBase','OpenDividendAccruals')
            ORDER BY a.report_date_local DESC,a.created_at_utc DESC,
                     a.raw_artifact_id DESC,r.raw_record_id DESC
        """), {"account_id": account_id}).mappings().all()
        sources = [InsightRow(**dict(row)) for row in rows]
        historical = [InsightRow(**dict(row)) for row in history]
        report = AccountInsights(sources, [r for r in historical if r.section == "EquitySummaryInBase"]).build()
        evidence = db_account_insight_evidence(connection, account_id)
        report["checks"] = account_calculation_checks(sources, evidence)
        report["income"] = account_income(sources, [r for r in historical if r.section == "OpenDividendAccruals"], evidence["cash"])
        report["lending"] = account_lending(sources)
        report["settled_cash"] = account_settled_cash(sources)
        return report
