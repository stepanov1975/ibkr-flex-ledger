"""Focused tests for incremental snapshot scope derivation."""

from datetime import date
from uuid import uuid4

from app.db.interfaces import RawRecordForCanonicalMapping
from app.jobs.incremental_scope import IncrementalSnapshotScope, job_build_incremental_snapshot_scope


def _raw_row(section_name: str, source_payload: dict[str, object]) -> RawRecordForCanonicalMapping:
    run_id = uuid4()
    return RawRecordForCanonicalMapping(
        raw_record_id=uuid4(),
        ingestion_run_id=run_id,
        account_id="U1",
        period_key="2026-08",
        flex_query_id="query",
        report_date_local=date(2026, 8, 21),
        section_name=section_name,
        source_row_ref=f"{section_name}:row-1",
        source_payload=source_payload,
    )


def test_scope_unions_event_conids_and_fx_source_currencies() -> None:
    scope = job_build_incremental_snapshot_scope([
        _raw_row("Trades", {"conid": "100"}),
        _raw_row("CashTransactions", {"conid": "200"}),
        _raw_row("CorporateActions", {"conid": "100"}),
        _raw_row("OpenPositions", {"conid": "300"}),
        _raw_row("ConversionRates", {"fromCurrency": "EUR"}),
    ])
    assert scope.conids == frozenset({"100", "200", "300"})
    assert scope.currencies == frozenset({"EUR"})
    assert scope.full_rebuild_reason is None


def test_scope_requests_full_rebuild_when_relevant_key_is_missing() -> None:
    scope = job_build_incremental_snapshot_scope([_raw_row("Trades", {"symbol": "AAA"})])
    assert scope.full_rebuild_reason == "unscopable_changed_row:Trades:missing_conid"


def test_scope_requests_full_rebuild_when_conid_is_not_a_string() -> None:
    scope = job_build_incremental_snapshot_scope([_raw_row("Trades", {"conid": None})])
    assert scope == IncrementalSnapshotScope(
        frozenset(), frozenset(), "unscopable_changed_row:Trades:missing_conid"
    )


def test_scope_requests_full_rebuild_when_fx_source_currency_is_not_a_string() -> None:
    scope = job_build_incremental_snapshot_scope([_raw_row("ConversionRates", {"fromCurrency": None})])
    assert scope == IncrementalSnapshotScope(
        frozenset(), frozenset(), "unscopable_changed_row:ConversionRates:missing_fromCurrency"
    )


def test_scope_is_empty_for_snapshot_irrelevant_sections() -> None:
    scope = job_build_incremental_snapshot_scope([_raw_row("AccountInformation", {"accountId": "U1"})])
    assert scope == IncrementalSnapshotScope(frozenset(), frozenset(), None)
