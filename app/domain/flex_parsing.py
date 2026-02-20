"""Shared Flex date and timestamp parsing helpers.

This module centralizes normalization logic used by multiple ingestion paths so
date and timestamp contracts remain deterministic across raw extraction and
canonical mapping.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

_DOMAIN_FLEX_TIMESTAMP_TZ_ABBREVIATION_TO_OFFSET = {
    "EST": "-0500",
    "EDT": "-0400",
}

_DOMAIN_FLEX_NULL_SENTINELS = frozenset({"-", "--", "N/A"})


def domain_flex_normalize_optional_text(value: object | None) -> str | None:
    """Normalize one optional Flex text value using shared null-sentinel policy.

    Args:
        value: Candidate value from Flex payload.

    Returns:
        str | None: Normalized text value or None when missing/sentinel.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        return None

    normalized_value = value.strip()
    if not normalized_value:
        return None
    if normalized_value in _DOMAIN_FLEX_NULL_SENTINELS:
        return None
    return normalized_value


def domain_flex_parse_local_date(value: str) -> date | None:
    """Parse one Flex local date value into `date`.

    Args:
        value: Candidate date text from Flex payload.

    Returns:
        date | None: Parsed date when supported, else None.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    normalized_value = value.strip()
    if not normalized_value:
        return None

    for candidate in _domain_flex_build_date_candidates(normalized_value):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            pass

        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            pass

        for supported_format in ("%Y/%m/%d", "%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(candidate, supported_format).date()
            except ValueError:
                continue

    return None


def domain_flex_parse_timestamp_to_utc_iso(value: str) -> str | None:
    """Parse one Flex timestamp and normalize it to UTC ISO-8601.

    Args:
        value: Candidate timestamp text from Flex payload.

    Returns:
        str | None: UTC ISO-8601 timestamp when supported, else None.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    normalized_value = value.strip()
    if not normalized_value:
        return None

    for candidate in _domain_flex_build_timestamp_candidates(normalized_value):
        try:
            parsed_value = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return _domain_flex_normalize_timestamp_to_utc_iso(parsed_value)

    for supported_format in ("%Y%m%d;%H%M%S", "%Y-%m-%d,%H:%M:%S"):
        try:
            parsed_value = datetime.strptime(normalized_value, supported_format)
        except ValueError:
            continue
        return _domain_flex_normalize_timestamp_to_utc_iso(parsed_value)

    timestamp_with_numeric_offset = _domain_flex_replace_ibkr_timezone_abbreviation_with_offset(normalized_value)
    if timestamp_with_numeric_offset is not None:
        try:
            parsed_value = datetime.strptime(timestamp_with_numeric_offset, "%d %B, %Y %I:%M %p %z")
        except ValueError:
            return None
        return _domain_flex_normalize_timestamp_to_utc_iso(parsed_value)

    return None


def _domain_flex_replace_ibkr_timezone_abbreviation_with_offset(normalized_value: str) -> str | None:
    """Replace known IBKR timezone abbreviations with numeric UTC offset.

    Args:
        normalized_value: Timestamp candidate value.

    Returns:
        str | None: Value with numeric offset when supported, else None.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    if len(normalized_value) < 3:
        return None

    timezone_abbreviation = normalized_value[-3:]
    timezone_offset = _DOMAIN_FLEX_TIMESTAMP_TZ_ABBREVIATION_TO_OFFSET.get(timezone_abbreviation)
    if timezone_offset is None:
        return None
    return f"{normalized_value[:-3]}{timezone_offset}"


def _domain_flex_build_date_candidates(normalized_value: str) -> list[str]:
    """Build deterministic date parse candidates for Flex values.

    Args:
        normalized_value: Stripped source date value.

    Returns:
        list[str]: Ordered de-duplicated candidate values.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    return _domain_flex_build_split_candidates(normalized_value=normalized_value, separators=(";", "T", " "))


def _domain_flex_build_timestamp_candidates(normalized_value: str) -> list[str]:
    """Build deterministic timestamp parse candidates for Flex values.

    Args:
        normalized_value: Stripped source timestamp value.

    Returns:
        list[str]: Ordered de-duplicated candidate values.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    candidate_values: list[str] = [normalized_value]
    if normalized_value.endswith("Z"):
        candidate_values.append(f"{normalized_value[:-1]}+00:00")
    if ";" in normalized_value:
        date_part, time_part = normalized_value.split(";", maxsplit=1)
        candidate_values.append(f"{date_part}T{time_part}")
    if "," in normalized_value:
        date_part, time_part = normalized_value.split(",", maxsplit=1)
        candidate_values.append(f"{date_part}T{time_part.strip()}")
    return _domain_flex_deduplicate_candidates(candidate_values)


def _domain_flex_build_split_candidates(normalized_value: str, separators: tuple[str, ...]) -> list[str]:
    """Build deterministic split candidates using configured separators.

    Args:
        normalized_value: Stripped source value.
        separators: Separators that may indicate trailing timestamp text.

    Returns:
        list[str]: Ordered de-duplicated candidates.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    candidate_values: list[str] = [normalized_value]
    for separator in separators:
        if separator in normalized_value:
            candidate_values.append(normalized_value.split(separator, maxsplit=1)[0])
    return _domain_flex_deduplicate_candidates(candidate_values)


def _domain_flex_deduplicate_candidates(candidate_values: list[str]) -> list[str]:
    """De-duplicate candidates while preserving insertion order.

    Args:
        candidate_values: Candidate parse values.

    Returns:
        list[str]: Ordered unique candidate values.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    seen_values: set[str] = set()
    unique_candidates: list[str] = []
    for candidate in candidate_values:
        if candidate in seen_values:
            continue
        seen_values.add(candidate)
        unique_candidates.append(candidate)
    return unique_candidates


def _domain_flex_normalize_timestamp_to_utc_iso(value: datetime) -> str:
    """Normalize datetime value to deterministic UTC ISO-8601 string.

    Args:
        value: Parsed timestamp value.

    Returns:
        str: UTC timestamp in ISO-8601 format.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).isoformat()
    return value.astimezone(timezone.utc).isoformat()


__all__ = [
    "domain_flex_normalize_optional_text",
    "domain_flex_parse_local_date",
    "domain_flex_parse_timestamp_to_utc_iso",
]
