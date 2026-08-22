"""Shared operational SLO status calculation and API serialization."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.analytics import IngestionSloSummary, analytics_ingestion_slo_summary
from app.db import IngestionSloRecord


@dataclass(frozen=True)
class OperationsSloStatus:
    measured_at_utc: datetime
    summary: IngestionSloSummary
    reason_codes: tuple[str, ...]

    @property
    def alerting(self) -> bool:
        return bool(self.reason_codes)

    def api_payload(self) -> dict[str, Any]:
        summary = self.summary
        return {
            "measured_at_utc": self.measured_at_utc.isoformat(),
            "window_days": 30,
            "run_count": summary.run_count,
            "success_count": summary.success_count,
            "success_rate": summary.success_rate,
            "success_target": summary.success_target,
            "success_alert_threshold": summary.success_alert_threshold,
            "success_breached": summary.success_breached,
            "p95_duration_ms": summary.p95_duration_ms,
            "p95_target_ms": summary.p95_target_ms,
            "duration_alert_threshold_ms": summary.duration_alert_threshold_ms,
            "duration_breached": summary.duration_breached,
            "consecutive_failure_alert": summary.consecutive_failure_alert,
            "alerting": self.alerting,
            "reason_codes": list(self.reason_codes),
            "owner": "app owner",
        }


def operations_build_slo_status(
    rows: list[IngestionSloRecord],
    measured_at_utc: datetime,
) -> OperationsSloStatus:
    summary = analytics_ingestion_slo_summary(rows)
    reasons = []
    if summary.success_breached:
        reasons.append("success_rate_below_threshold")
    if summary.duration_breached:
        reasons.append("duration_above_threshold")
    if summary.consecutive_failure_alert:
        reasons.append("consecutive_failures")
    return OperationsSloStatus(measured_at_utc, summary, tuple(reasons))
