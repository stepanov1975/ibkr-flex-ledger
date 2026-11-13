"""Shared broker OpenPositions valuation rules."""

OPEN_POSITIONS_SUMMARY_UNAVAILABLE = "openpositions_summary_unavailable"


def db_open_position_is_summary(alias: str) -> str:
    """Treat legacy rows without a detail field as summary candidates."""
    return f"COALESCE(NULLIF(UPPER(BTRIM({alias}.source_payload->>'levelOfDetail')), ''), 'SUMMARY')='SUMMARY'"
