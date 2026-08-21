"""Reliability SLO calculations for ingestion operations."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from app.db import IngestionSloRecord


@dataclass(frozen=True)
class IngestionSloSummary:
    run_count: int
    success_count: int
    success_rate: float | None
    success_target: float
    success_alert_threshold: float
    success_breached: bool
    p95_duration_ms: int | None
    p95_target_ms: int
    duration_alert_threshold_ms: int
    duration_breached: bool
    consecutive_failure_alert: bool


def analytics_ingestion_slo_summary(rows: list[IngestionSloRecord]) -> IngestionSloSummary:
    """Calculate the frozen success-rate and duration SLO signals."""

    completed = [row for row in rows if row.status in {"success", "failed"}]
    success_count = sum(row.status == "success" for row in completed)
    success_rate = None if not completed else success_count / len(completed)
    durations = sorted(row.duration_ms for row in completed if row.duration_ms is not None)
    p95_duration_ms = None
    if durations:
        p95_duration_ms = durations[max(0, ceil(len(durations) * 0.95) - 1)]
    last_two = completed[-2:]
    return IngestionSloSummary(
        run_count=len(completed),
        success_count=success_count,
        success_rate=success_rate,
        success_target=0.99,
        success_alert_threshold=0.98,
        success_breached=success_rate is not None and success_rate < 0.98,
        p95_duration_ms=p95_duration_ms,
        p95_target_ms=15 * 60 * 1000,
        duration_alert_threshold_ms=30 * 60 * 1000,
        duration_breached=any(duration > 30 * 60 * 1000 for duration in durations),
        consecutive_failure_alert=len(last_two) == 2 and all(row.status == "failed" for row in last_two),
    )
