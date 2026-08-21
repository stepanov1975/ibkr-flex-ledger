"""Ingestion reliability SLO regression tests."""

from datetime import datetime, timedelta, timezone

from app.analytics import analytics_ingestion_slo_summary
from app.db import IngestionSloRecord


def _row(status: str, minutes: int) -> IngestionSloRecord:
    started = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    return IngestionSloRecord(
        status=status,
        started_at_utc=started,
        ended_at_utc=started + timedelta(minutes=minutes),
        duration_ms=minutes * 60 * 1000,
    )


def test_slo_summary_reports_healthy_window() -> None:
    """Keep success and duration signals clear within frozen thresholds."""

    summary = analytics_ingestion_slo_summary([_row("success", 4), _row("success", 6)])

    assert summary.success_rate == 1.0
    assert summary.p95_duration_ms == 360_000
    assert summary.success_breached is False
    assert summary.duration_breached is False
    assert summary.consecutive_failure_alert is False


def test_slo_summary_alerts_on_slow_consecutive_failures() -> None:
    """Trigger every operational signal for a breached ingestion window."""

    summary = analytics_ingestion_slo_summary([_row("success", 2), _row("failed", 31), _row("failed", 1)])

    assert summary.success_breached is True
    assert summary.duration_breached is True
    assert summary.consecutive_failure_alert is True
