"""Regression tests for deterministic canonical reprocess workflow."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.db.interfaces import IngestionRunRecord, IngestionRunReference, IngestionRunState
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
        self.trade_instrument_ids: list[str] = []

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
        self.trade_instrument_ids.append(request.instrument_id)

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


class _IngestionRepositoryStub:
    """Capture reprocess run finalize diagnostics for assertions."""

    def __init__(self):
        """Initialize deterministic run record and capture buffer.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self._run_id = uuid4()
        self.finalize_calls: list[dict[str, object]] = []

    def db_ingestion_run_create_started(self, account_id, run_type, period_key, flex_query_id, report_date_local):
        """Return deterministic started run record.

        Args:
            account_id: Account identifier.
            run_type: Run type.
            period_key: Period key.
            flex_query_id: Flex query id.
            report_date_local: Optional report date.

        Returns:
            IngestionRunRecord: Started run record.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = (account_id, run_type, period_key, flex_query_id, report_date_local)
        return IngestionRunRecord(
            ingestion_run_id=self._run_id,
            account_id="U_TEST",
            run_type="reprocess",
            reference=IngestionRunReference(
                period_key="2026-02-14",
                flex_query_id="query",
                report_date_local=None,
            ),
            state=IngestionRunState(
                status="started",
                started_at_utc=None,
                ended_at_utc=None,
                duration_ms=None,
                error_code=None,
                error_message=None,
                diagnostics=None,
            ),
            created_at_utc=None,
        )

    def db_ingestion_run_finalize(self, ingestion_run_id, status, error_code, error_message, diagnostics):
        """Capture finalize call diagnostics.

        Args:
            ingestion_run_id: Run identifier.
            status: Final status.
            error_code: Optional error code.
            error_message: Optional error message.
            diagnostics: Timeline diagnostics payload.

        Returns:
            IngestionRunRecord: Minimal finalized record.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.finalize_calls.append(
            {
                "ingestion_run_id": ingestion_run_id,
                "status": status,
                "error_code": error_code,
                "error_message": error_message,
                "diagnostics": diagnostics,
            }
        )
        return IngestionRunRecord(
            ingestion_run_id=ingestion_run_id,
            account_id="U_TEST",
            run_type="reprocess",
            reference=IngestionRunReference(
                period_key="2026-02-14",
                flex_query_id="query",
                report_date_local=None,
            ),
            state=IngestionRunState(
                status=status,
                started_at_utc=None,
                ended_at_utc=None,
                duration_ms=None,
                error_code=error_code,
                error_message=error_message,
                diagnostics=diagnostics,
            ),
            created_at_utc=None,
        )



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
    assert all(len(instrument_id) == 36 for instrument_id in canonical_repository.trade_instrument_ids)


def test_jobs_reprocess_persists_canonical_duration_diagnostics() -> None:
    """Persist canonical stage duration diagnostics in reprocess timeline.

    Returns:
        None: Assertions validate diagnostics payload.

    Raises:
        AssertionError: Raised when duration field is absent or invalid.
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
    ingestion_repository = _IngestionRepositoryStub()

    orchestrator = CanonicalReprocessOrchestrator(
        raw_read_repository=raw_read_repository,
        canonical_persistence_repository=canonical_repository,
        config=CanonicalReprocessOrchestratorConfig(
            account_id="U_TEST",
            period_key="2026-02-14",
            flex_query_id="query",
            functional_currency="USD",
        ),
        ingestion_repository=ingestion_repository,
    )

    result = orchestrator.job_execute(job_name="reprocess_run")

    assert result.status == "success"
    diagnostics = ingestion_repository.finalize_calls[0]["diagnostics"]
    canonical_completed = [
        event
        for event in diagnostics
        if event.get("stage") == "canonical_mapping" and event.get("status") == "completed"
    ]
    assert len(canonical_completed) == 1
    details = canonical_completed[0].get("details")
    assert isinstance(details, dict)
    assert "canonical_duration_ms" in details
    assert isinstance(details["canonical_duration_ms"], int)
    assert details["canonical_duration_ms"] >= 0
