"""Shared timeline event helper utilities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def domain_build_stage_event(
    stage: str,
    status: str,
    details: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Build one structured timeline event payload.

    Args:
        stage: Stage name.
        status: Stage status marker.
        details: Optional structured details object.

    Returns:
        dict[str, object]: Structured timeline event.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    event_payload: dict[str, object] = {
        "stage": stage,
        "status": status,
        "at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if details is not None:
        event_payload["details"] = details
    return event_payload
