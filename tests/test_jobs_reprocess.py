"""Regression tests for deterministic canonical reprocess workflow."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.jobs.reprocess_orchestrator import (
    CanonicalReprocessOrchestrator,
    CanonicalReprocessOrchestratorConfig,
)
from app.mapping.service import RawRecordForMapping


class _RawReadRepositoryStub:
    """Return deterministic raw rows for each reprocess execution."""

    def __init__(self, rows: list[RawRecordForMapping]):
        """Initialize deterministic rows.

        Args:
            rows: Raw rows returned for replay.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self._rows = rows

    def db_raw_record_list_for_period(self, account_id: str, period_key: str, flex_query_id: str) -> list[RawRecordForMapping]:
        """Return deterministic period rows.

        Args:
            account_id: Account identifier.
            period_key: Period identity key.
            flex_query_id: Flex query identifier.

        Returns:
            list[RawRecordForMapping]: Deterministic source rows.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = (account_id, period_key, flex_query_id)
        return self._rows

    def db_raw_row_count(self) -> int:
        """Return deterministic row count for test diagnostics.

        Returns:
            int: Number of replayable rows.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return len(self._rows)


class _CanonicalPersistRepositoryStub:
    """Capture upserted canonical identifiers to assert determinism."""

    def __init__(self):
        """Initialize capture container.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.upserted_trade_exec_ids: list[str] = []

    def db_canonical_instrument_upsert(self, request):
        """Return deterministic instrument record for each upsert request.

        Args:
            request: Canonical instrument upsert request.

        Returns:
            object: Lightweight instrument record object.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return type("InstrumentRecord", (), {"instrument_id": uuid4(), "account_id": request.account_id, "conid": request.conid})()

    def db_canonical_trade_fill_upsert(self, request) -> None:
        """Capture upserted trade execution ids.

        Args:
            request: Canonical trade upsert request.

        Returns:
            None: Captured as side effect.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.upserted_trade_exec_ids.append(request.ib_exec_id)

    def db_canonical_cashflow_upsert(self, request) -> None:
        """Capture cashflow upsert calls.

        Args:
            request: Canonical cashflow upsert request.

        Returns:
            None: Captured as side effect.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = request

    def db_canonical_fx_upsert(self, request) -> None:
        """Capture FX upsert calls.

        Args:
            request: Canonical FX upsert request.

        Returns:
            None: Captured as side effect.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = request

    def db_canonical_corp_action_upsert(self, request) -> None:
        """Capture corporate-action upsert calls.

        Args:
            request: Canonical corporate-action upsert request.

        Returns:
            None: Captured as side effect.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = request



def test_jobs_reprocess_is_deterministic_for_identical_raw_inputs() -> None:
    """Produce stable canonical identities across repeated reprocess runs.

    Returns:
        None: Assertions validate deterministic replay behavior.

    Raises:
        AssertionError: Raised when replay output identities diverge.
    """

    raw_rows = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="Trades",
            source_row_ref="Trades:Trade:transactionID=1001",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "ibExecID": "EXEC-1001",
                "transactionID": "1001",
                "conid": "265598",
                "buySell": "BUY",
                "quantity": "10",
                "tradePrice": "100.10",
                "currency": "USD",
                "reportDate": "2026-02-14",
                "dateTime": "2026-02-14T15:20:00+00:00",
            },
        )
    ]
    raw_read_repository = _RawReadRepositoryStub(rows=raw_rows)
    canonical_repository = _CanonicalPersistRepositoryStub()

    orchestrator = CanonicalReprocessOrchestrator(
        raw_read_repository=raw_read_repository,
        canonical_persistence_repository=canonical_repository,
        config=CanonicalReprocessOrchestratorConfig(
            account_id="U_TEST",
            period_key="2026-02-14",
            flex_query_id="query",
            functional_currency="USD",
        ),
    )

    first_result = orchestrator.job_execute(job_name="reprocess_run")
    second_result = orchestrator.job_execute(job_name="reprocess_run")

    assert first_result.status == "success"
    assert second_result.status == "success"
    assert canonical_repository.upserted_trade_exec_ids == ["EXEC-1001", "EXEC-1001"]
