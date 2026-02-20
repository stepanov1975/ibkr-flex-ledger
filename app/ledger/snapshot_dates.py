"""Timezone helpers for Task 7 daily snapshot business-date boundaries."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


_TASK7_LOCAL_REPORT_TZ = ZoneInfo("Asia/Jerusalem")


def snapshot_resolve_report_date_local(timestamp_utc: str) -> str:
    """Resolve local report date in Asia/Jerusalem from UTC timestamp.

    Args:
        timestamp_utc: Offset-aware UTC ISO-8601 timestamp.

    Returns:
        str: Local report date in YYYY-MM-DD format.

    Raises:
        ValueError: Raised when timestamp is blank, malformed, or offset-naive.
    """

    if not isinstance(timestamp_utc, str) or not timestamp_utc.strip():
        raise ValueError("timestamp_utc must be a non-empty string")

    try:
        parsed_timestamp = datetime.fromisoformat(timestamp_utc)
    except ValueError as error:
        raise ValueError(f"timestamp_utc must be a valid ISO-8601 timestamp: {timestamp_utc}") from error

    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
        raise ValueError("timestamp_utc must be offset-aware")

    return parsed_timestamp.astimezone(_TASK7_LOCAL_REPORT_TZ).date().isoformat()


__all__ = ["snapshot_resolve_report_date_local"]
