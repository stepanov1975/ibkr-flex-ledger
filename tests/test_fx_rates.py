"""Regression coverage for indexed conversion-rate selection."""

from dataclasses import replace
from datetime import date
from uuid import UUID

from app.db.interfaces import LedgerFxRateRecord
from app.domain.fx_rates import ConversionRateHistory


def test_conversion_history_preserves_normalized_pair_and_source_precedence() -> None:
    first = LedgerFxRateRecord(
        report_date_local=date(2026, 8, 20), currency=" eur ", functional_currency=" usd ",
        fx_rate="1.2", fx_source="conversion_rates", ingestion_run_id=UUID(int=1), source_raw_record_id=UUID(int=2),
    )
    same_day = replace(first, currency="EUR", functional_currency="USD", fx_rate="1.3", source_raw_record_id=UUID(int=3))
    next_day = replace(first, report_date_local=date(2026, 8, 21), fx_rate="1.4")
    missing = replace(next_day, fx_rate=None)
    zero = replace(next_day, fx_rate="0")
    other_pair = replace(next_day, functional_currency="ILS", fx_rate="4")
    nonfinite = [replace(next_day, fx_rate=rate) for rate in ("Infinity", "NaN")]
    history = ConversionRateHistory([first, same_day, next_day, missing, zero, *nonfinite, other_pair])

    assert history.select("EUR", "USD", date(2026, 8, 19)) is None
    assert history.select("EUR ", " UsD", date(2026, 8, 20)) is same_day
    assert history.select("eur", "usd", date(2026, 8, 22)) is next_day
    assert history.select("EUR", "ILS", date(2026, 8, 22)) is other_pair
    assert history.select("JPY", "USD", date(2026, 8, 22)) is None


def test_conversion_history_preserves_last_eligible_row_for_unordered_evidence() -> None:
    older = LedgerFxRateRecord(
        report_date_local=date(2026, 8, 20), currency="EUR", functional_currency="USD", fx_rate="1.2",
        fx_source="conversion_rates", ingestion_run_id=UUID(int=1), source_raw_record_id=UUID(int=2),
    )
    newer = replace(older, report_date_local=date(2026, 8, 22), fx_rate="1.3")
    latest = replace(newer, report_date_local=date(2026, 8, 23), fx_rate="1.4")
    history = ConversionRateHistory([newer, older, latest])

    assert history.select("EUR", "USD", newer.report_date_local) is older
    assert history.select("EUR", "USD", latest.report_date_local) is latest
