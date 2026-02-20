"""Regression tests for shared canonical mapping pipeline behavior."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.db.interfaces import RawRecordForCanonicalMapping
from app.jobs.canonical_pipeline import job_canonical_map_and_persist


class _CanonicalPipelineRepositoryStub:
    """Deterministic canonical persistence stub for pipeline tests."""

    def __init__(self) -> None:
        """Initialize deterministic stub state.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.instrument_upsert_calls = 0
        self.bulk_upsert_calls = 0

    def db_canonical_instrument_upsert(self, request):
        """Capture instrument upsert invocation and return deterministic identity.

        Args:
            request: Canonical instrument upsert request.

        Returns:
            object: Minimal instrument record object.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.instrument_upsert_calls += 1
        return type(
            "InstrumentRecord",
            (),
            {"instrument_id": uuid4(), "account_id": request.account_id, "conid": request.conid},
        )()

    def db_canonical_bulk_upsert(self, trade_requests, cashflow_requests, fx_requests, corp_action_requests) -> None:
        """Capture bulk upsert invocation.

        Args:
            trade_requests: Canonical trade requests.
            cashflow_requests: Canonical cashflow requests.
            fx_requests: Canonical fx requests.
            corp_action_requests: Canonical corporate-action requests.

        Returns:
            None: Captured as side effect.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = (trade_requests, cashflow_requests, fx_requests, corp_action_requests)
        self.bulk_upsert_calls += 1


def test_jobs_canonical_pipeline_reports_unique_instrument_upsert_count() -> None:
    """Report instrument upsert count as unique conid writes, not raw request volume.

    Returns:
        None: Assertions validate canonical diagnostics counting contract.

    Raises:
        AssertionError: Raised when count reflects duplicate mapping requests.
    """

    repository_stub = _CanonicalPipelineRepositoryStub()
    ingestion_run_id = uuid4()
    raw_records = [
        RawRecordForCanonicalMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=ingestion_run_id,
            account_id="U_TEST",
            period_key="2026-02-14",
            flex_query_id="query",
            report_date_local=date(2026, 2, 14),
            section_name="Trades",
            source_row_ref="Trades:Trade:transactionID=1001",
            source_payload={
                "ibExecID": "EXEC-1001",
                "transactionID": "1001",
                "conid": "265598",
                "buySell": "BUY",
                "quantity": "1",
                "tradePrice": "100",
                "currency": "USD",
                "reportDate": "2026-02-14",
                "dateTime": "2026-02-14T10:00:00+00:00",
            },
        ),
        RawRecordForCanonicalMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=ingestion_run_id,
            account_id="U_TEST",
            period_key="2026-02-14",
            flex_query_id="query",
            report_date_local=date(2026, 2, 14),
            section_name="Trades",
            source_row_ref="Trades:Trade:transactionID=1002",
            source_payload={
                "ibExecID": "EXEC-1002",
                "transactionID": "1002",
                "conid": "265598",
                "buySell": "SELL",
                "quantity": "1",
                "tradePrice": "101",
                "currency": "USD",
                "reportDate": "2026-02-14",
                "dateTime": "2026-02-14T11:00:00+00:00",
            },
        ),
    ]

    result_counts = job_canonical_map_and_persist(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
        canonical_persistence_repository=repository_stub,
    )

    assert repository_stub.bulk_upsert_calls == 1
    assert repository_stub.instrument_upsert_calls == 1
    assert result_counts["instrument_upsert_count"] == 1
    assert result_counts["trade_fill_count"] == 2
