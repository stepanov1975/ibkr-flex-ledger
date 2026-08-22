from datetime import datetime, timedelta, timezone

from app.db import IngestionSloRecord
from app.operations import operations_build_slo_status


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _row(status: str, minutes: int) -> IngestionSloRecord:
    return IngestionSloRecord(
        status=status,
        started_at_utc=NOW - timedelta(minutes=minutes),
        ended_at_utc=NOW,
        duration_ms=minutes * 60_000,
    )


def test_operations_slo_status_reports_all_active_reasons() -> None:
    status = operations_build_slo_status(
        [_row("success", 2), _row("failed", 31), _row("failed", 1)],
        measured_at_utc=NOW,
    )

    assert status.alerting is True
    assert status.reason_codes == (
        "success_rate_below_threshold",
        "duration_above_threshold",
        "consecutive_failures",
    )
    assert status.api_payload()["measured_at_utc"] == NOW.isoformat()
    assert status.api_payload()["owner"] == "app owner"


def test_operations_slo_status_reports_healthy_empty_window() -> None:
    status = operations_build_slo_status([], measured_at_utc=NOW)

    assert status.alerting is False
    assert status.reason_codes == ()
    assert status.api_payload()["success_rate"] is None
