"""Real PostgreSQL source version and account isolation tests."""

from decimal import Decimal

from app.analytics.account_insights import _CHANGE_COMPONENTS, _NAV_COMPONENTS
from app.db.account_insights import db_account_insights
from test_ingestion_integrity_regressions import database, _harness  # noqa: F401
from test_end_to_end_seeded import _SEEDED_PAYLOAD


def test_insights_successful_whole_sections_and_history(database):  # noqa: F811
    orchestrator, adapter, _, _, _, _, _ = _harness(database)
    nav_zeroes = " ".join(f'{key}="0"' for key in _NAV_COMPONENTS if key not in ("cash", "stock"))
    movement_zeroes = " ".join(f'{key}="0"' for key in _CHANGE_COMPONENTS)
    extra = (f'<EquitySummaryInBase><EquitySummaryByReportDateInBase reportDate="20260821" currency="USD" total="250" cash="30" stock="220" {nav_zeroes}/></EquitySummaryInBase>'
             f'<ChangeInNAV currency="USD" fromDate="20260820" toDate="20260821" startingValue="240" endingValue="250" mtm="10" {movement_zeroes}/>').encode()
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b"</FlexStatement>", extra + b"</FlexStatement>")
    assert orchestrator.job_execute("ingestion_run").status == "success"
    result = db_account_insights(database, "INTEGRITY")
    assert result["nav"]["check"]["status"] == "matched"
    assert result["change_in_nav"]["check"]["status"] == "matched"
    assert db_account_insights(database, "OTHER")["nav"] is None
    # A conflicting execution retains raw bytes but cannot replace successful NAV.
    adapter.payload_bytes = adapter.payload_bytes.replace(b'total="250"', b'total="999"').replace(
        b'tradePrice="100"', b'tradePrice="999"')
    assert orchestrator.job_execute("ingestion_run").status == "failed"
    assert db_account_insights(database, "INTEGRITY")["nav"]["total"] == Decimal("250")
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b"</FlexStatement>", extra.replace(
        b'total="250"', b'total="251"') + b"</FlexStatement>")
    assert orchestrator.job_execute("ingestion_run").status == "success"
    result = db_account_insights(database, "INTEGRITY")
    assert len(result["nav_history"]) == 1
    assert result["nav_history"][0]["nav"] == Decimal("251")
    # Explicit empty current section must not resurrect historical positions.
    adapter.payload_bytes = adapter.payload_bytes.replace(
        b'<OpenPositions><OpenPosition', b'<Ignored><OpenPosition').replace(
        b'</OpenPositions>', b'</Ignored><OpenPositions/>')
    assert orchestrator.job_execute("ingestion_run").status == "success"
    assert db_account_insights(database, "INTEGRITY")["nav"]["cash_and_positions"] == Decimal("30")
