"""Regression tests for deterministic canonical reprocess workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, NoReturn, TypedDict, cast
from uuid import UUID, uuid4

import pytest

from app import bootstrap as bootstrap_module
from app import main as main_module
from app.db import SnapshotCleanupCandidate
from app.db.interfaces import (
    CanonicalCashflowUpsertRequest,
    CanonicalCorpActionUpsertRequest,
    CanonicalFxUpsertRequest,
    CanonicalInstrumentRecord,
    CanonicalInstrumentUpsertRequest,
    CanonicalPersistenceRepositoryPort,
    CanonicalTradeFillUpsertRequest,
    IngestionRunRecord,
    IngestionRunReference,
    IngestionRunRepositoryPort,
    IngestionRunState,
    LedgerSnapshotRepositoryPort,
    RawArtifactReplayCandidate,
    RawRecordReadRepositoryPort,
)
from app.jobs import reprocess_orchestrator as reprocess_module
from app.jobs.reprocess_orchestrator import (
    CanonicalReprocessOrchestrator,
    CanonicalReprocessOrchestratorConfig,
    job_select_replay_artifacts,
)
from app.adapters import FlexStatementError
from app.ledger import SnapshotBuildResult, StockLedgerSnapshotService
from app.mapping.service import RawRecordForMapping


def _candidate(
    report_date: date,
    created_at: datetime,
    artifact_id: UUID,
    *,
    open_positions_present: bool = True,
) -> RawArtifactReplayCandidate:
    return RawArtifactReplayCandidate(
        raw_artifact_id=artifact_id,
        ingestion_run_id=uuid4(),
        report_date_local=report_date,
        created_at_utc=created_at,
        open_positions_present=open_positions_present,
    )


def test_reprocess_selects_newest_artifact_per_actual_report_date() -> None:
    report_date = date(2026, 2, 19)
    older = _candidate(report_date, datetime(2026, 2, 20, tzinfo=timezone.utc), UUID(int=1))
    newer_low_uuid = _candidate(report_date, datetime(2026, 2, 21, tzinfo=timezone.utc), UUID(int=2))
    newer_high_uuid = _candidate(report_date, datetime(2026, 2, 21, tzinfo=timezone.utc), UUID(int=3))
    later_date = _candidate(date(2026, 8, 20), datetime(2026, 8, 21, tzinfo=timezone.utc), UUID(int=4))

    selected = job_select_replay_artifacts([
        later_date, newer_low_uuid, older, newer_high_uuid
    ])

    assert selected == (newer_high_uuid, later_date)


def test_reprocess_rejects_selected_artifact_without_open_positions() -> None:
    candidate = _candidate(
        date(2026, 8, 20),
        datetime(2026, 8, 21, tzinfo=timezone.utc),
        UUID(int=5),
        open_positions_present=False,
    )
    with pytest.raises(ValueError, match="OpenPositions"):
        job_select_replay_artifacts([candidate])


def test_reprocess_selection_is_empty_without_candidates() -> None:
    assert job_select_replay_artifacts([]) == ()


def test_reprocess_empty_selection_finalizes_failed() -> None:
    """Reject an explicit replay scope that resolves to no artifacts."""

    operation_log: list[tuple[object, ...]] = []
    ingestion_repository = _IngestionRepositoryStub()
    orchestrator = _reprocess_orchestrator(
        raw_read_repository=_ArtifactRawRepository([], {}, operation_log),
        canonical_persistence_repository=_CanonicalPersistRepositoryStub(),
        snapshot_service=_SnapshotServiceStub(operation_log),
        snapshot_repository=_CleanupRepositoryStub(operation_log),
        config=CanonicalReprocessOrchestratorConfig("U_TEST", "2026-02-20", "query", "USD"),
        ingestion_repository=ingestion_repository,
    )

    result = orchestrator.job_execute_reprocess_target("2026-02-20", "query")

    assert result.status == "failed"
    assert ingestion_repository.finalize_calls[-1]["error_code"] == "REPROCESS_CONTRACT_ERROR"
    assert "ABORT_EMPTY_SELECTION" in str(ingestion_repository.finalize_calls[-1]["error_message"])
    assert "U_TEST" not in str(ingestion_repository.finalize_calls[-1]["error_message"])
    assert "U_TEST" not in str(ingestion_repository.finalize_calls[-1]["diagnostics"])


def test_reprocess_finalizes_unexpected_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not leave a reprocess run active after an ordinary programming error."""

    operation_log: list[tuple[object, ...]] = []
    candidate = _candidate(date(2026, 8, 20), datetime(2026, 8, 21, tzinfo=timezone.utc), UUID(int=9))
    ingestion_repository = _IngestionRepositoryStub()
    orchestrator = _reprocess_orchestrator(
        raw_read_repository=_ArtifactRawRepository([candidate], {}, operation_log),
        canonical_persistence_repository=_CanonicalPersistRepositoryStub(),
        snapshot_service=_SnapshotServiceStub(operation_log),
        snapshot_repository=_CleanupRepositoryStub(operation_log),
        config=CanonicalReprocessOrchestratorConfig("U_TEST", "2026-02-20", "query", "USD"),
        ingestion_repository=ingestion_repository,
    )

    def raise_unexpected(_candidates: list[RawArtifactReplayCandidate]) -> tuple[RawArtifactReplayCandidate, ...]:
        raise KeyError("unexpected selection failure")

    monkeypatch.setattr(reprocess_module, "job_select_replay_artifacts", raise_unexpected)

    result = orchestrator.job_execute("reprocess_run")

    assert result.status == "failed"
    assert ingestion_repository.finalize_calls[-1]["status"] == "failed"
    assert ingestion_repository.finalize_calls[-1]["error_code"] == "REPROCESS_UNEXPECTED_ERROR"
    assert "unexpected selection failure" in str(ingestion_repository.finalize_calls[-1]["error_message"])


class _ArtifactRawRepository:
    def __init__(
        self,
        candidates: list[RawArtifactReplayCandidate],
        rows_by_artifact: dict[UUID, list[RawRecordForMapping]],
        operation_log: list[tuple[object, ...]],
    ) -> None:
        self.candidates = candidates
        self.rows_by_artifact = rows_by_artifact
        self.operation_log = operation_log
        self.captured_scope: tuple[str, str, str] | None = None

    def db_raw_artifact_replay_candidate_list(
        self, account_id: str, period_key: str, flex_query_id: str
    ) -> list[RawArtifactReplayCandidate]:
        self.captured_scope = (account_id, period_key, flex_query_id)
        return self.candidates

    def db_raw_record_list_for_artifact(
        self, raw_artifact_id: UUID
    ) -> list[RawRecordForMapping]:
        self.operation_log.append(("read_artifact", raw_artifact_id))
        return self.rows_by_artifact[raw_artifact_id]


class _CanonicalPersistRepositoryStub:
    """Capture upserted canonical identifiers to assert determinism."""

    def __init__(self) -> None:
        """Initialize capture container.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self.upserted_trade_exec_ids: list[str] = []
        self.trade_instrument_ids: list[str] = []

    def db_canonical_instrument_upsert_many(
        self, requests: list[CanonicalInstrumentUpsertRequest]
    ) -> list[CanonicalInstrumentRecord]:
        """Return deterministic instrument records for each batch request.

        Args:
            requests: Canonical instrument upsert requests.

        Returns:
            list[CanonicalInstrumentRecord]: Canonical instrument identity records.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return [
            CanonicalInstrumentRecord(
                instrument_id=uuid4(),
                account_id=request.account_id,
                conid=request.conid,
            )
            for request in requests
        ]

    def db_canonical_trade_fill_upsert(
        self,
        request: CanonicalTradeFillUpsertRequest,
    ) -> None:
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

    def db_canonical_cashflow_upsert(
        self,
        request: CanonicalCashflowUpsertRequest,
    ) -> None:
        """Capture cashflow upsert calls.

        Args:
            request: Canonical cashflow upsert request.

        Returns:
            None: Captured as side effect.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = request

    def db_canonical_fx_upsert(self, request: CanonicalFxUpsertRequest) -> None:
        """Capture FX upsert calls.

        Args:
            request: Canonical FX upsert request.

        Returns:
            None: Captured as side effect.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = request

    def db_canonical_corp_action_upsert(
        self,
        request: CanonicalCorpActionUpsertRequest,
    ) -> None:
        """Capture corporate-action upsert calls.

        Args:
            request: Canonical corporate-action upsert request.

        Returns:
            None: Captured as side effect.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = request

    def db_canonical_bulk_upsert(
        self,
        trade_requests: list[CanonicalTradeFillUpsertRequest],
        cashflow_requests: list[CanonicalCashflowUpsertRequest],
        fx_requests: list[CanonicalFxUpsertRequest],
        corp_action_requests: list[CanonicalCorpActionUpsertRequest],
    ) -> None:
        """Capture canonical bulk upsert calls via per-request delegations.

        Args:
            trade_requests: Canonical trade requests.
            cashflow_requests: Canonical cashflow requests.
            fx_requests: Canonical FX requests.
            corp_action_requests: Canonical corporate-action requests.

        Returns:
            None: Captured as side effect.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        for trade_request in trade_requests:
            self.db_canonical_trade_fill_upsert(trade_request)
        for cashflow_request in cashflow_requests:
            self.db_canonical_cashflow_upsert(cashflow_request)
        for fx_request in fx_requests:
            self.db_canonical_fx_upsert(fx_request)
        for corp_action_request in corp_action_requests:
            self.db_canonical_corp_action_upsert(corp_action_request)


class _FinalizeCall(TypedDict):
    ingestion_run_id: UUID
    status: str
    error_code: str | None
    error_message: str | None
    diagnostics: list[dict[str, Any]] | None


class _IngestionRepositoryStub:
    """Capture reprocess run finalize diagnostics for assertions."""

    def __init__(self) -> None:
        """Initialize deterministic run record and capture buffer.

        Returns:
            None: Initializer does not return values.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        self._run_id = uuid4()
        self.finalize_calls: list[_FinalizeCall] = []

    def db_ingestion_run_create_started(
        self,
        account_id: str,
        run_type: str,
        period_key: str,
        flex_query_id: str,
        report_date_local: date | None,
    ) -> IngestionRunRecord:
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
                started_at_utc=cast(datetime, None),
                ended_at_utc=None,
                duration_ms=None,
                error_code=None,
                error_message=None,
                diagnostics=None,
            ),
            created_at_utc=cast(datetime, None),
        )

    def db_ingestion_run_finalize(
        self,
        ingestion_run_id: UUID,
        status: str,
        error_code: str | None,
        error_message: str | None,
        diagnostics: list[dict[str, Any]] | None,
    ) -> IngestionRunRecord:
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
                started_at_utc=cast(datetime, None),
                ended_at_utc=None,
                duration_ms=None,
                error_code=error_code,
                error_message=error_message,
                diagnostics=diagnostics,
            ),
            created_at_utc=cast(datetime, None),
        )


class _SnapshotServiceStub:
    def __init__(
        self,
        operation_log: list[tuple[object, ...]],
        fail_on_call: int | None = None,
    ) -> None:
        self.operation_log = operation_log
        self.fail_on_call = fail_on_call
        self.calls: list[dict[str, object]] = []

    def ledger_snapshot_build_and_persist(
        self,
        account_id: str,
        ingestion_run_id: str | None,
        report_date_local: str,
        functional_currency: str,
        affected_conids: frozenset[str] | None = None,
        affected_currencies: frozenset[str] | None = None,
    ) -> SnapshotBuildResult:
        _ = (affected_conids, affected_currencies)
        self.calls.append({
            "account_id": account_id,
            "ingestion_run_id": ingestion_run_id,
            "report_date_local": report_date_local,
            "functional_currency": functional_currency,
        })
        candidate_date = date.fromisoformat(report_date_local)
        self.operation_log.append(("snapshot", candidate_date, UUID(ingestion_run_id or "")))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("snapshot failure")
        return SnapshotBuildResult(
            report_date_local,
            1,
            0,
            0,
            broker_position_match_count=2,
            broker_position_mismatch_count=1,
            broker_only_position_count=1,
            broker_absent_nonzero_fifo_count=1,
        )


class _CleanupRepositoryStub:
    def __init__(self, operation_log: list[tuple[object, ...]]) -> None:
        self.operation_log = operation_log
        self.delete_calls: list[tuple[str, ...]] = []

    def db_pnl_snapshot_daily_unsupported_list(
        self,
        account_id: str,
        period_key: str,
        flex_query_id: str,
        supported_report_dates: tuple[str, ...],
    ) -> list[SnapshotCleanupCandidate]:
        _ = (account_id, period_key, flex_query_id)
        self.operation_log.append(("list_cleanup", supported_report_dates))
        return [SnapshotCleanupCandidate(date(2026, 2, 21), 44)]

    def db_pnl_snapshot_daily_unsupported_delete(
        self,
        account_id: str,
        period_key: str,
        flex_query_id: str,
        supported_report_dates: tuple[str, ...],
    ) -> int:
        _ = (account_id, period_key, flex_query_id)
        self.delete_calls.append(supported_report_dates)
        self.operation_log.append(("delete_cleanup", supported_report_dates))
        return 44


def _reprocess_orchestrator(
    *,
    raw_read_repository: _ArtifactRawRepository,
    canonical_persistence_repository: _CanonicalPersistRepositoryStub,
    snapshot_service: _SnapshotServiceStub,
    snapshot_repository: _CleanupRepositoryStub,
    config: CanonicalReprocessOrchestratorConfig,
    ingestion_repository: _IngestionRepositoryStub | None = None,
) -> CanonicalReprocessOrchestrator:
    """Inject deliberately partial test doubles at the production boundary."""

    return CanonicalReprocessOrchestrator(
        raw_read_repository=cast(RawRecordReadRepositoryPort, raw_read_repository),
        canonical_persistence_repository=cast(
            CanonicalPersistenceRepositoryPort,
            canonical_persistence_repository,
        ),
        snapshot_service=cast(StockLedgerSnapshotService, snapshot_service),
        snapshot_repository=cast(
            LedgerSnapshotRepositoryPort,
            snapshot_repository,
        ),
        config=config,
        ingestion_repository=(
            cast(IngestionRunRepositoryPort, ingestion_repository)
            if ingestion_repository is not None
            else None
        ),
    )


class _CliReprocessOrchestratorStub:
    def __init__(self) -> None:
        self.default_calls: list[str] = []
        self.target_calls: list[tuple[str, str]] = []
        self.cleanup_target_calls: list[tuple[str, str]] = []

    def job_execute(self, job_name: str) -> SimpleNamespace:
        self.default_calls.append(job_name)
        return SimpleNamespace(status="success")

    def job_execute_reprocess_target(
        self,
        period_key: str,
        flex_query_id: str,
    ) -> SimpleNamespace:
        self.target_calls.append((period_key, flex_query_id))
        return SimpleNamespace(status="success")

    def job_execute_reprocess_target_with_cleanup(
        self,
        period_key: str,
        flex_query_id: str,
    ) -> SimpleNamespace:
        self.cleanup_target_calls.append((period_key, flex_query_id))
        return SimpleNamespace(status="success")


def _trade_row(owner_run_id: UUID, artifact_label: str) -> RawRecordForMapping:
    return RawRecordForMapping(
        raw_record_id=uuid4(),
        ingestion_run_id=owner_run_id,
        section_name="Trades",
        source_row_ref=f"Trades:Trade:transactionID={artifact_label}",
        report_date_local=date(2026, 8, 20),
        source_payload={
            "artifact_label": artifact_label,
            "ibExecID": f"EXEC-{artifact_label}",
            "transactionID": artifact_label,
            "levelOfDetail": "EXECUTION",
            "conid": "265598",
            "buySell": "BUY",
            "quantity": "1",
            "tradePrice": "10",
            "currency": "USD",
            "reportDate": "20260820",
            "dateTime": "2026-08-20T10:00:00+00:00",
        },
    )


@dataclass(frozen=True)
class _ReprocessHarness:
    orchestrator: CanonicalReprocessOrchestrator
    cleanup_repository: _CleanupRepositoryStub
    ingestion_repository: _IngestionRepositoryStub


def _build_reprocess_harness(
    operation_log: list[tuple[object, ...]],
    fail_on_snapshot_call: int | None = None,
) -> _ReprocessHarness:
    first = _candidate(date(2026, 2, 19), datetime(2026, 2, 20, tzinfo=timezone.utc), UUID(int=11))
    second = _candidate(date(2026, 8, 20), datetime(2026, 8, 21, tzinfo=timezone.utc), UUID(int=12))
    raw_repository = _ArtifactRawRepository(
        [first, second],
        {
            first.raw_artifact_id: [_trade_row(first.ingestion_run_id, "first")],
            second.raw_artifact_id: [_trade_row(second.ingestion_run_id, "second")],
        },
        operation_log,
    )
    cleanup_repository = _CleanupRepositoryStub(operation_log)
    ingestion_repository = _IngestionRepositoryStub()
    orchestrator = _reprocess_orchestrator(
        raw_read_repository=raw_repository,
        canonical_persistence_repository=_CanonicalPersistRepositoryStub(),
        snapshot_service=_SnapshotServiceStub(operation_log, fail_on_snapshot_call),
        snapshot_repository=cleanup_repository,
        config=CanonicalReprocessOrchestratorConfig("U_TEST", "2026-02-20", "query", "USD"),
        ingestion_repository=ingestion_repository,
    )
    return _ReprocessHarness(orchestrator, cleanup_repository, ingestion_repository)



def test_reprocess_maps_and_snapshots_selected_artifacts_chronologically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_log: list[tuple[object, ...]] = []
    first = _candidate(date(2026, 2, 19), datetime(2026, 2, 20, tzinfo=timezone.utc), UUID(int=1))
    second = _candidate(date(2026, 8, 20), datetime(2026, 8, 21, tzinfo=timezone.utc), UUID(int=2))
    rows_by_artifact = {
        first.raw_artifact_id: [_trade_row(first.ingestion_run_id, "first")],
        second.raw_artifact_id: [_trade_row(second.ingestion_run_id, "second")],
    }

    def capture_map(**kwargs: object) -> dict[str, int]:
        raw_rows = kwargs["raw_records"]
        assert isinstance(raw_rows, list)
        artifact_label = raw_rows[0].source_payload["artifact_label"]
        operation_log.append(("map", artifact_label))
        return {"instrument_upsert_count": 1, "trade_fill_count": 1,
                "cashflow_count": 0, "fx_count": 0, "corp_action_count": 0}

    monkeypatch.setattr(reprocess_module, "job_canonical_map_and_persist", capture_map)
    raw_repository = _ArtifactRawRepository(
        [second, first], rows_by_artifact, operation_log
    )
    snapshot_service = _SnapshotServiceStub(operation_log)
    cleanup_repository = _CleanupRepositoryStub(operation_log)
    orchestrator = _reprocess_orchestrator(
        raw_read_repository=raw_repository,
        canonical_persistence_repository=_CanonicalPersistRepositoryStub(),
        snapshot_service=snapshot_service,
        snapshot_repository=cleanup_repository,
        config=CanonicalReprocessOrchestratorConfig("U_TEST", "2026-02-20", "query", "USD"),
        ingestion_repository=_IngestionRepositoryStub(),
    )

    result = orchestrator.job_execute_reprocess_target_with_cleanup("2026-02-20", "query")

    supported_dates = ("2026-02-19", "2026-08-20")
    assert result.status == "success"
    assert operation_log == [
        ("read_artifact", first.raw_artifact_id),
        ("map", "first"),
        ("snapshot", first.report_date_local, first.ingestion_run_id),
        ("read_artifact", second.raw_artifact_id),
        ("map", "second"),
        ("snapshot", second.report_date_local, second.ingestion_run_id),
        ("list_cleanup", supported_dates),
        ("delete_cleanup", supported_dates),
    ]
    assert [call["functional_currency"] for call in snapshot_service.calls] == ["USD", "USD"]


def test_reprocess_failure_never_deletes_unsupported_snapshots() -> None:
    operation_log: list[tuple[object, ...]] = []
    harness = _build_reprocess_harness(operation_log, fail_on_snapshot_call=2)
    result = harness.orchestrator.job_execute_reprocess_target_with_cleanup("2026-02-20", "query")
    assert result.status == "failed"
    assert harness.cleanup_repository.delete_calls == []
    assert not any(operation[0] == "list_cleanup" for operation in operation_log)


def test_default_reprocess_does_not_cleanup_unsupported_dates() -> None:
    operation_log: list[tuple[object, ...]] = []
    harness = _build_reprocess_harness(operation_log)
    result = harness.orchestrator.job_execute("reprocess_run")
    assert result.status == "success"
    assert harness.cleanup_repository.delete_calls == []


def test_scoped_reprocess_does_not_cleanup_unsupported_dates() -> None:
    operation_log: list[tuple[object, ...]] = []
    harness = _build_reprocess_harness(operation_log)

    result = harness.orchestrator.job_execute_reprocess_target("2026-02-20", "query")

    assert result.status == "success"
    assert harness.cleanup_repository.delete_calls == []


def test_reprocess_records_cleanup_candidates_before_deleted_count() -> None:
    harness = _build_reprocess_harness([])
    result = harness.orchestrator.job_execute_reprocess_target_with_cleanup("2026-02-20", "query")
    assert result.status == "success"
    diagnostics = harness.ingestion_repository.finalize_calls[0]["diagnostics"]
    assert diagnostics is not None
    cleanup_events = [event for event in diagnostics if event["stage"] == "snapshot_cleanup"]
    assert cleanup_events[0]["details"]["candidates"] == [
        {"report_date_local": "2026-02-21", "row_count": 44}
    ]
    assert cleanup_events[1]["details"]["deleted_row_count"] == 44


def test_jobs_reprocess_is_deterministic_for_identical_raw_inputs() -> None:
    operation_log: list[tuple[object, ...]] = []
    candidate = _candidate(date(2026, 2, 14), datetime(2026, 2, 15, tzinfo=timezone.utc), UUID(int=21))
    raw_read_repository = _ArtifactRawRepository(
        [candidate],
        {candidate.raw_artifact_id: [_trade_row(candidate.ingestion_run_id, "1001")]},
        operation_log,
    )
    canonical_repository = _CanonicalPersistRepositoryStub()
    cleanup_repository = _CleanupRepositoryStub(operation_log)
    orchestrator = _reprocess_orchestrator(
        raw_read_repository=raw_read_repository,
        canonical_persistence_repository=canonical_repository,
        snapshot_service=_SnapshotServiceStub(operation_log),
        snapshot_repository=cleanup_repository,
        config=CanonicalReprocessOrchestratorConfig("U_TEST", "2026-02-14", "query", "USD"),
    )

    first_result = orchestrator.job_execute(job_name="reprocess_run")
    second_result = orchestrator.job_execute(job_name="reprocess_run")

    assert first_result.status == "success"
    assert second_result.status == "success"
    assert canonical_repository.upserted_trade_exec_ids == ["EXEC-1001", "EXEC-1001"]
    assert all(len(instrument_id) == 36 for instrument_id in canonical_repository.trade_instrument_ids)
    assert cleanup_repository.delete_calls == []


def test_jobs_reprocess_persists_canonical_duration_diagnostics() -> None:
    operation_log: list[tuple[object, ...]] = []
    harness = _build_reprocess_harness(operation_log)

    result = harness.orchestrator.job_execute(job_name="reprocess_run")

    assert result.status == "success"
    diagnostics = harness.ingestion_repository.finalize_calls[0]["diagnostics"]
    assert diagnostics is not None
    canonical_completed = [
        event
        for event in diagnostics
        if event.get("stage") == "canonical_mapping" and event.get("status") == "completed"
    ]
    assert len(canonical_completed) == 2
    for event in canonical_completed:
        details = event.get("details")
        assert isinstance(details, dict)
        assert isinstance(details["canonical_duration_ms"], int)
        assert details["canonical_duration_ms"] >= 0


def test_jobs_reprocess_persists_snapshot_reconciliation_counts() -> None:
    harness = _build_reprocess_harness([])

    result = harness.orchestrator.job_execute(job_name="reprocess_run")

    assert result.status == "success"
    diagnostics = harness.ingestion_repository.finalize_calls[0]["diagnostics"]
    assert diagnostics is not None
    snapshot_completed = [
        event
        for event in diagnostics
        if event.get("stage") == "snapshot" and event.get("status") == "completed"
    ]
    assert len(snapshot_completed) == 2
    details = snapshot_completed[0]["details"]
    assert details["broker_position_match_count"] == 2
    assert details["broker_position_mismatch_count"] == 1
    assert details["broker_only_position_count"] == 1
    assert details["broker_absent_nonzero_fifo_count"] == 1


def test_jobs_reprocess_explicit_scope_override_uses_requested_period_and_query() -> None:
    operation_log: list[tuple[object, ...]] = []
    candidate = _candidate(date(2026, 2, 14), datetime(2026, 2, 15, tzinfo=timezone.utc), UUID(int=31))
    raw_repository = _ArtifactRawRepository(
        [candidate],
        {candidate.raw_artifact_id: [_trade_row(candidate.ingestion_run_id, "scope")]},
        operation_log,
    )
    cleanup_repository = _CleanupRepositoryStub(operation_log)
    orchestrator = _reprocess_orchestrator(
        raw_read_repository=raw_repository,
        canonical_persistence_repository=_CanonicalPersistRepositoryStub(),
        snapshot_service=_SnapshotServiceStub(operation_log),
        snapshot_repository=cleanup_repository,
        config=CanonicalReprocessOrchestratorConfig("U_TEST", "2026-02-01", "query-default", "USD"),
    )

    execution_result = orchestrator.job_execute_reprocess_target(
        period_key="2026-02-12",
        flex_query_id="query-override",
    )

    assert execution_result.status == "success"
    assert raw_repository.captured_scope == ("U_TEST", "2026-02-12", "query-override")
    assert cleanup_repository.delete_calls == []


def test_jobs_reprocess_maps_typed_statement_error_to_deterministic_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_log: list[tuple[object, ...]] = []
    raw_read_repository = _ArtifactRawRepository([], {}, operation_log)

    def _raise_typed_statement_error(
        account_id: str,
        period_key: str,
        flex_query_id: str,
    ) -> NoReturn:
        _ = (account_id, period_key, flex_query_id)
        raise FlexStatementError("statement failed", error_code="1017")

    monkeypatch.setattr(
        raw_read_repository,
        "db_raw_artifact_replay_candidate_list",
        _raise_typed_statement_error,
    )
    ingestion_repository = _IngestionRepositoryStub()

    orchestrator = _reprocess_orchestrator(
        raw_read_repository=raw_read_repository,
        canonical_persistence_repository=_CanonicalPersistRepositoryStub(),
        snapshot_service=_SnapshotServiceStub(operation_log),
        snapshot_repository=_CleanupRepositoryStub(operation_log),
        config=CanonicalReprocessOrchestratorConfig("U_TEST", "2026-02-14", "query", "USD"),
        ingestion_repository=ingestion_repository,
    )

    result = orchestrator.job_execute(job_name="reprocess_run")

    assert result.status == "failed"
    assert ingestion_repository.finalize_calls[0]["error_code"] == "REPROCESS_STATEMENT_ERROR"


def test_reprocess_bootstrap_provides_snapshot_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        account_id="U_TEST",
        database_url="postgresql+psycopg://test",
        ibkr_flex_query_id="query",
    )
    engine = object()
    operation_log: list[tuple[object, ...]] = []
    candidate = _candidate(date(2026, 2, 20), datetime(2026, 2, 21, tzinfo=timezone.utc), UUID(int=41))

    class _BootstrapCanonicalRepository(
        _CanonicalPersistRepositoryStub,
        _ArtifactRawRepository,
    ):
        def __init__(self) -> None:
            _CanonicalPersistRepositoryStub.__init__(self)
            _ArtifactRawRepository.__init__(
                self,
                [candidate],
                {candidate.raw_artifact_id: [_trade_row(candidate.ingestion_run_id, "bootstrap")]},
                operation_log,
            )

    ingestion_repository = _IngestionRepositoryStub()
    canonical_repository = _BootstrapCanonicalRepository()
    snapshot_repository = _CleanupRepositoryStub(operation_log)
    snapshot_service = _SnapshotServiceStub(operation_log)
    snapshot_service_repositories: list[object] = []

    def build_snapshot_service(*, repository: object) -> _SnapshotServiceStub:
        snapshot_service_repositories.append(repository)
        return snapshot_service

    monkeypatch.setattr(bootstrap_module, "config_load_settings", lambda: settings)
    monkeypatch.setattr(bootstrap_module, "db_create_engine", lambda database_url: engine)
    monkeypatch.setattr(
        bootstrap_module,
        "SQLAlchemyIngestionRunService",
        lambda *, engine: ingestion_repository,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "SQLAlchemyCanonicalPersistenceService",
        lambda *, engine: canonical_repository,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "SQLAlchemyLedgerSnapshotService",
        lambda *, engine: snapshot_repository,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "StockLedgerSnapshotService",
        build_snapshot_service,
    )

    orchestrator = bootstrap_module.bootstrap_create_reprocess_orchestrator(
        period_key="2026-02-20",
        flex_query_id="query",
    )

    result = orchestrator.job_execute("reprocess_run")

    assert result.status == "success"
    assert snapshot_service_repositories == [snapshot_repository]
    assert snapshot_service.calls[0]["functional_currency"] == "USD"
    assert snapshot_repository.delete_calls == []


def test_reprocess_cli_uses_explicit_cleanup_only_with_complete_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator = _CliReprocessOrchestratorStub()
    monkeypatch.setattr(
        main_module,
        "bootstrap_create_reprocess_orchestrator",
        lambda period_key, flex_query_id: orchestrator,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "stock-app",
            "reprocess-run",
            "--period-key",
            "2026-02-20",
            "--flex-query-id",
            "query",
        ],
    )

    main_module.main()

    assert orchestrator.cleanup_target_calls == [("2026-02-20", "query")]
    assert orchestrator.target_calls == []
    assert orchestrator.default_calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["stock-app", "reprocess-run"],
        ["stock-app", "reprocess-run", "--period-key", "2026-02-20"],
        ["stock-app", "reprocess-run", "--flex-query-id", "query"],
    ],
)
def test_reprocess_cli_without_complete_scope_uses_default_execution(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    orchestrator = _CliReprocessOrchestratorStub()
    monkeypatch.setattr(
        main_module,
        "bootstrap_create_reprocess_orchestrator",
        lambda period_key, flex_query_id: orchestrator,
    )
    monkeypatch.setattr("sys.argv", arguments)

    main_module.main()

    assert orchestrator.default_calls == ["reprocess_run"]
    assert orchestrator.target_calls == []
    assert orchestrator.cleanup_target_calls == []
