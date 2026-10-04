"""Successful Flex section selection for read-only account insights."""

from sqlalchemy import Engine, text
from typing import Any

from app.analytics.account_commissions import account_commissions
from app.analytics.account_options import account_option_activity
from app.analytics.account_holdings import account_lending, account_settled_cash, account_concentration
from app.analytics.account_income import account_income
from app.analytics.account_checks import account_calculation_checks
from .account_insight_evidence import db_account_insight_evidence
from .published_flex_sections import db_published_flex_sections_history, db_published_flex_sections_latest
from app.analytics.account_insights import AccountInsights, InsightRow

_SECTIONS = ("EquitySummaryInBase", "CashReport", "OpenPositions", "ChangeInNAV",
             "FIFOPerformanceSummaryInBase", "MTMPerformanceSummaryInBase",
             "OpenDividendAccruals", "ChangeInDividendAccruals", "InterestAccruals", "NetStockPositionSummary", "OptionEAE", "UnbundledCommissionDetails")


def db_account_insights(engine: Engine, account_id: str) -> dict[str, Any]:
    """Read coherent successful sources; failed reports never replace published data."""
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        latest_sources = db_published_flex_sections_latest(account_id, _SECTIONS).cte("published_sections")
        rows = connection.execute(text("""
            SELECT r.section_name AS section,r.source_payload AS payload,r.raw_record_id::text AS raw_id,
                   a.raw_artifact_id::text AS artifact_id,a.report_date_local AS report_date
            FROM published_sections a
            JOIN raw_record r ON r.raw_artifact_id=a.raw_artifact_id AND r.section_name=a.section_name
            ORDER BY r.section_name,r.source_row_ref,r.raw_record_id
        """).columns().add_cte(latest_sources)).mappings().all()
        historical_sources = db_published_flex_sections_history(
            account_id, ("EquitySummaryInBase", "OpenDividendAccruals"),
        ).cte("published_sections")
        history = connection.execute(text("""
            SELECT
                r.section_name AS section,r.source_payload AS payload,r.raw_record_id::text AS raw_id,
                a.raw_artifact_id::text AS artifact_id,a.report_date_local AS report_date
            FROM published_sections a
            JOIN raw_record r ON r.raw_artifact_id=a.raw_artifact_id AND r.section_name=a.section_name
            ORDER BY a.report_date_local DESC,a.created_at_utc DESC,
                     a.raw_artifact_id DESC,r.raw_record_id DESC
        """).columns().add_cte(historical_sources)).mappings().all()
        sources = [InsightRow(**dict(row)) for row in rows]
        historical = [InsightRow(**dict(row)) for row in history]
        report = AccountInsights(sources, [r for r in historical if r.section == "EquitySummaryInBase"]).build()
        evidence = db_account_insight_evidence(connection, account_id)
        report["checks"] = account_calculation_checks(sources, evidence)
        report["income"] = account_income(sources, [r for r in historical if r.section == "OpenDividendAccruals"], evidence["cash"])
        report["lending"] = account_lending(sources)
        report["settled_cash"] = account_settled_cash(sources)
        report["option_activity"] = account_option_activity(sources, evidence["trades"])
        report["concentration"] = account_concentration(sources)
        report["commissions"] = account_commissions(sources, evidence["trades"])
        return report
