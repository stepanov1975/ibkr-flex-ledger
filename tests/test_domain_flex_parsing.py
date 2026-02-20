"""Regression tests for shared Flex date and timestamp parsing helpers."""

from datetime import date

from app.domain.flex_parsing import (
    domain_flex_normalize_optional_text,
    domain_flex_parse_local_date,
    domain_flex_parse_timestamp_to_utc_iso,
)


def test_domain_flex_normalize_optional_text_maps_known_null_sentinels_to_none() -> None:
    """Map known IBKR null sentinels to None for optional text values.

    Returns:
        None: Assertions validate deterministic null-sentinel normalization.

    Raises:
        AssertionError: Raised when null-sentinel values are not normalized.
    """

    assert domain_flex_normalize_optional_text(None) is None
    assert domain_flex_normalize_optional_text(1) is None
    assert domain_flex_normalize_optional_text("") is None
    assert domain_flex_normalize_optional_text("  ") is None
    assert domain_flex_normalize_optional_text("-") is None
    assert domain_flex_normalize_optional_text("--") is None
    assert domain_flex_normalize_optional_text("N/A") is None
    assert domain_flex_normalize_optional_text(" value ") == "value"


def test_domain_flex_parse_local_date_accepts_supported_variants() -> None:
    """Parse common Flex report-date variants into deterministic date values.

    Returns:
        None: Assertions validate deterministic date parsing behavior.

    Raises:
        AssertionError: Raised when supported date variants are not parsed.
    """

    assert domain_flex_parse_local_date("2026-02-14") == date(2026, 2, 14)
    assert domain_flex_parse_local_date("20260214") == date(2026, 2, 14)
    assert domain_flex_parse_local_date("2026/02/14") == date(2026, 2, 14)
    assert domain_flex_parse_local_date("02/14/2026") == date(2026, 2, 14)
    assert domain_flex_parse_local_date("02/14/26") == date(2026, 2, 14)
    assert domain_flex_parse_local_date("14-Feb-26") == date(2026, 2, 14)
    assert domain_flex_parse_local_date("2026-02-14T10:00:00+00:00") == date(2026, 2, 14)


def test_domain_flex_parse_local_date_returns_none_for_invalid_values() -> None:
    """Return None for unsupported or blank local date values.

    Returns:
        None: Assertions validate invalid date handling.

    Raises:
        AssertionError: Raised when invalid values are parsed unexpectedly.
    """

    assert domain_flex_parse_local_date("") is None
    assert domain_flex_parse_local_date("14-02-2026") is None


def test_domain_flex_parse_timestamp_to_utc_iso_normalizes_supported_variants() -> None:
    """Normalize supported Flex timestamp variants into UTC ISO-8601 strings.

    Returns:
        None: Assertions validate deterministic UTC normalization.

    Raises:
        AssertionError: Raised when timestamp normalization is incorrect.
    """

    assert domain_flex_parse_timestamp_to_utc_iso("2026-02-14T12:00:00+02:00") == "2026-02-14T10:00:00+00:00"
    assert domain_flex_parse_timestamp_to_utc_iso("2026-02-14T10:00:00Z") == "2026-02-14T10:00:00+00:00"
    assert domain_flex_parse_timestamp_to_utc_iso("20260214;101500") == "2026-02-14T10:15:00+00:00"
    assert domain_flex_parse_timestamp_to_utc_iso("20 February, 2026 02:15 PM EST") == "2026-02-20T19:15:00+00:00"
    assert domain_flex_parse_timestamp_to_utc_iso("20 February, 2026 02:15 PM EDT") == "2026-02-20T18:15:00+00:00"


def test_domain_flex_parse_timestamp_to_utc_iso_returns_none_for_invalid_values() -> None:
    """Return None for unsupported or blank timestamp values.

    Returns:
        None: Assertions validate invalid timestamp handling.

    Raises:
        AssertionError: Raised when invalid values are parsed unexpectedly.
    """

    assert domain_flex_parse_timestamp_to_utc_iso("") is None
    assert domain_flex_parse_timestamp_to_utc_iso("2026/02/14 10:00:00") is None
