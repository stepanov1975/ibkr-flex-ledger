"""Job-layer orchestrator for deterministic canonical reprocess workflows."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import traceback

from app.adapters import FlexAdapterConnectionError, FlexAdapterTimeoutError, FlexRequestError, FlexStatementError
from app.db import (
    CanonicalPersistenceRepositoryPort,
    IngestionRunRepositoryPort,
    LedgerSnapshotRepositoryPort,
    RawArtifactReplayCandidate,
    RawRecordReadRepositoryPort,
)
from app.domain import domain_build_stage_event
from app.ledger import StockLedgerSnapshotService

from .canonical_pipeline import job_canonical_map_and_persist
from .interfaces import JobExecutionResult, JobOrchestratorPort


def job_select_replay_artifacts(
    candidates: list[RawArtifactReplayCandidate],
) -> tuple[RawArtifactReplayCandidate, ...]:
    """Select the newest replayable artifact for each actual report date."""

    selected_by_date: dict[date, RawArtifactReplayCandidate] = {}
    for candidate in candidates:
        current = selected_by_date.get(candidate.report_date_local)
        if current is None or (
            candidate.created_at_utc,
            candidate.raw_artifact_id,
        ) > (
            current.created_at_utc,
            current.raw_artifact_id,
        ):
            selected_by_date[candidate.report_date_local] = candidate
    selected = tuple(
        sorted(
            selected_by_date.values(),
            key=lambda item: (
                item.report_date_local,
                item.created_at_utc,
                item.raw_artifact_id,
            ),
        )
    )
    missing = [item.raw_artifact_id for item in selected if not item.open_positions_present]
    if missing:
        raise ValueError(f"selected artifacts missing OpenPositions section: {missing}")
    return selected


@dataclass(frozen=True)
class CanonicalReprocessOrchestratorConfig:
    """Configuration values for canonical reprocess execution.

    Attributes:
        account_id: Internal single-account context identifier.
        period_key: Ingestion period identity key for replay scope.
        flex_query_id: Upstream Flex query identifier.
        functional_currency: Functional/base reporting currency code.
    """

    account_id: str
    period_key: str
    flex_query_id: str
    functional_currency: str = "USD"


class CanonicalReprocessOrchestrator(JobOrchestratorPort):
    """Concrete orchestrator for canonical replay from immutable raw rows."""

    _REPROCESS_JOB_NAME = "reprocess_run"

    def __init__(
        self,
        raw_read_repository: RawRecordReadRepositoryPort,
        canonical_persistence_repository: CanonicalPersistenceRepositoryPort,
        snapshot_service: StockLedgerSnapshotService,
        snapshot_repository: LedgerSnapshotRepositoryPort,
        config: CanonicalReprocessOrchestratorConfig,
        ingestion_repository: IngestionRunRepositoryPort | None = None,
    ):
        """Initialize canonical reprocess dependencies.

        Args:
            raw_read_repository: Raw-row read repository.
            canonical_persistence_repository: Canonical persistence repository.
            snapshot_service: Ledger snapshot build service.
            snapshot_repository: Ledger snapshot cleanup repository.
            config: Reprocess configuration values.
            ingestion_repository: Optional ingestion run repository for timeline persistence.

        Returns:
            None: Initializer does not return values.

        Raises:
            ValueError: Raised when dependencies or config values are invalid.
        """

        if raw_read_repository is None:
            raise ValueError("raw_read_repository must not be None")
        if canonical_persistence_repository is None:
            raise ValueError("canonical_persistence_repository must not be None")
        if snapshot_service is None:
            raise ValueError("snapshot_service must not be None")
        if snapshot_repository is None:
            raise ValueError("snapshot_repository must not be None")
        if not config.account_id.strip():
            raise ValueError("config.account_id must not be blank")
        if not config.period_key.strip():
            raise ValueError("config.period_key must not be blank")
        if not config.flex_query_id.strip():
            raise ValueError("config.flex_query_id must not be blank")
        if not config.functional_currency.strip():
            raise ValueError("config.functional_currency must not be blank")

        self._raw_read_repository = raw_read_repository
        self._canonical_persistence_repository = canonical_persistence_repository
        self._snapshot_service = snapshot_service
        self._snapshot_repository = snapshot_repository
        self._config = config
        self._ingestion_repository = ingestion_repository

    def job_supported_names(self) -> tuple[str, ...]:
        """Return supported job names.

        Returns:
            tuple[str, ...]: Supported job names.

        Raises:
            RuntimeError: This implementation does not raise runtime errors.
        """

        return (self._REPROCESS_JOB_NAME,)

    def job_execute(self, job_name: str) -> JobExecutionResult:
        """Execute canonical reprocess from immutable raw records.

        Args:
            job_name: Name of job to execute.

        Returns:
            JobExecutionResult: Final execution status payload.

        Raises:
            ValueError: Raised when job name is unsupported.
            RuntimeError: Raised for unexpected execution failures after finalization.
        """

        normalized_job_name = job_name.strip()
        if normalized_job_name != self._REPROCESS_JOB_NAME:
            raise ValueError(f"unsupported job_name={normalized_job_name}")

        return self._job_reprocess_execute_with_config(
            config=self._config,
            allow_unsupported_snapshot_cleanup=False,
        )

    def job_execute_reprocess_target(self, period_key: str, flex_query_id: str) -> JobExecutionResult:
        """Execute non-cleanup canonical reprocess for one explicit target.

        Args:
            period_key: Ingestion period identity key for replay scope.
            flex_query_id: Upstream Flex query identifier for replay scope.

        Returns:
            JobExecutionResult: Final execution status payload.

        Raises:
            ValueError: Raised when explicit scope values are invalid.
            RuntimeError: Raised for unexpected execution failures after finalization.
        """

        return self._job_reprocess_execute_with_config(
            config=self._job_reprocess_scoped_config(period_key, flex_query_id),
            allow_unsupported_snapshot_cleanup=False,
        )

    def job_execute_reprocess_target_with_cleanup(
        self,
        period_key: str,
        flex_query_id: str,
    ) -> JobExecutionResult:
        """Execute the operator-only cleanup-capable explicit reprocess path."""

        return self._job_reprocess_execute_with_config(
            config=self._job_reprocess_scoped_config(period_key, flex_query_id),
            allow_unsupported_snapshot_cleanup=True,
        )

    def _job_reprocess_scoped_config(
        self,
        period_key: str,
        flex_query_id: str,
    ) -> CanonicalReprocessOrchestratorConfig:
        """Validate explicit replay scope values without granting cleanup authority."""

        normalized_period_key = self._job_reprocess_validate_period_key(period_key)
        if not isinstance(flex_query_id, str):
            raise ValueError("flex_query_id must be a string")
        normalized_flex_query_id = flex_query_id.strip()
        if not normalized_flex_query_id:
            raise ValueError("flex_query_id must not be blank")
        return replace(
            self._config,
            period_key=normalized_period_key,
            flex_query_id=normalized_flex_query_id,
        )

    def _job_reprocess_execute_with_config(
        self,
        config: CanonicalReprocessOrchestratorConfig,
        allow_unsupported_snapshot_cleanup: bool,
    ) -> JobExecutionResult:
        """Execute canonical reprocess using the provided replay scope config.

        Args:
            config: Effective replay scope values.
            allow_unsupported_snapshot_cleanup: Whether explicit scope cleanup may delete rows.

        Returns:
            JobExecutionResult: Final execution status payload.

        Raises:
            RuntimeError: Raised for unexpected execution failures after finalization.
        """

        timeline: list[dict[str, object]] = [domain_build_stage_event(stage="run", status="started")]
        run_record = None

        if self._ingestion_repository is not None:
            run_record = self._ingestion_repository.db_ingestion_run_create_started(
                account_id=config.account_id,
                run_type="reprocess",
                period_key=config.period_key,
                flex_query_id=config.flex_query_id,
                report_date_local=None,
            )

        try:
            timeline.append(domain_build_stage_event(stage="raw_read", status="started"))
            candidates = self._raw_read_repository.db_raw_artifact_replay_candidate_list(
                account_id=config.account_id,
                period_key=config.period_key,
                flex_query_id=config.flex_query_id,
            )
            selected = job_select_replay_artifacts(candidates)
            if not selected:
                raise ValueError(
                    "ABORT_EMPTY_SELECTION: no replayable artifacts found for "
                    f"period_key={config.period_key} flex_query_id={config.flex_query_id}"
                )
            timeline.append(
                domain_build_stage_event(
                    stage="raw_read",
                    status="completed",
                    details={
                        "candidate_count": len(candidates),
                        "selected_artifact_count": len(selected),
                        "selected_report_dates": [
                            candidate.report_date_local.isoformat() for candidate in selected
                        ],
                    },
                )
            )

            for candidate in selected:
                artifact_details = {
                    "raw_artifact_id": str(candidate.raw_artifact_id),
                    "ingestion_run_id": str(candidate.ingestion_run_id),
                    "report_date_local": candidate.report_date_local.isoformat(),
                }
                timeline.append(
                    domain_build_stage_event(
                        stage="artifact_raw_read",
                        status="started",
                        details=artifact_details,
                    )
                )
                raw_rows = self._raw_read_repository.db_raw_record_list_for_artifact(
                    raw_artifact_id=candidate.raw_artifact_id,
                )
                raw_row_run_ids = {row.ingestion_run_id for row in raw_rows}
                if len(raw_row_run_ids) != 1:
                    raise RuntimeError("raw artifact rows must reference exactly one ingestion run")
                semantic_run_id = next(iter(raw_row_run_ids))
                artifact_details["ingestion_run_id"] = str(semantic_run_id)
                timeline.append(
                    domain_build_stage_event(
                        stage="artifact_raw_read",
                        status="completed",
                        details={**artifact_details, "raw_row_count": len(raw_rows)},
                    )
                )

                timeline.append(
                    domain_build_stage_event(
                        stage="canonical_mapping",
                        status="started",
                        details=artifact_details,
                    )
                )
                canonical_started_at = datetime.now(timezone.utc)
                canonical_counts = job_canonical_map_and_persist(
                    account_id=config.account_id,
                    functional_currency=config.functional_currency,
                    raw_records=raw_rows,
                    canonical_persistence_repository=self._canonical_persistence_repository,
                )
                canonical_duration_ms = max(
                    0,
                    int((datetime.now(timezone.utc) - canonical_started_at).total_seconds() * 1000),
                )
                timeline.append(
                    domain_build_stage_event(
                        stage="canonical_mapping",
                        status="completed",
                        details={
                            **artifact_details,
                            **canonical_counts,
                            "canonical_duration_ms": canonical_duration_ms,
                        },
                    )
                )

                timeline.append(
                    domain_build_stage_event(
                        stage="snapshot",
                        status="started",
                        details=artifact_details,
                    )
                )
                snapshot_result = self._snapshot_service.ledger_snapshot_build_and_persist(
                    account_id=config.account_id,
                    ingestion_run_id=str(semantic_run_id),
                    report_date_local=candidate.report_date_local.isoformat(),
                    functional_currency=config.functional_currency,
                )
                timeline.append(
                    domain_build_stage_event(
                        stage="snapshot",
                        status="completed",
                        details={
                            **artifact_details,
                            "snapshot_row_count": snapshot_result.snapshot_row_count,
                            "position_lot_row_count": snapshot_result.position_lot_row_count,
                            "missing_solid_valuation_count": snapshot_result.missing_solid_valuation_count,
                            "broker_position_match_count": snapshot_result.broker_position_match_count,
                            "broker_position_mismatch_count": snapshot_result.broker_position_mismatch_count,
                            "broker_only_position_count": snapshot_result.broker_only_position_count,
                            "broker_absent_nonzero_fifo_count": snapshot_result.broker_absent_nonzero_fifo_count,
                        },
                    )
                )

            supported_report_dates = tuple(
                candidate.report_date_local.isoformat() for candidate in selected
            )
            if supported_report_dates:
                cleanup_candidates = self._snapshot_repository.db_pnl_snapshot_daily_unsupported_list(
                    account_id=config.account_id,
                    period_key=config.period_key,
                    flex_query_id=config.flex_query_id,
                    supported_report_dates=supported_report_dates,
                )
                timeline.append(
                    domain_build_stage_event(
                        stage="snapshot_cleanup",
                        status="completed",
                        details={
                            "candidates": [
                                {
                                    "report_date_local": candidate.report_date_local.isoformat(),
                                    "row_count": candidate.row_count,
                                }
                                for candidate in cleanup_candidates
                            ]
                        },
                    )
                )
                if allow_unsupported_snapshot_cleanup:
                    deleted_row_count = self._snapshot_repository.db_pnl_snapshot_daily_unsupported_delete(
                        account_id=config.account_id,
                        period_key=config.period_key,
                        flex_query_id=config.flex_query_id,
                        supported_report_dates=supported_report_dates,
                    )
                    timeline.append(
                        domain_build_stage_event(
                            stage="snapshot_cleanup",
                            status="completed",
                            details={"deleted_row_count": deleted_row_count},
                        )
                    )

            timeline.append(domain_build_stage_event(stage="run", status="success"))
            ingestion_repository = self._ingestion_repository
            if run_record is not None and ingestion_repository is not None:
                ingestion_repository.db_ingestion_run_finalize(
                    ingestion_run_id=run_record.ingestion_run_id,
                    status="success",
                    error_code=None,
                    error_message=None,
                    diagnostics=timeline,
                )
            return JobExecutionResult(job_name=self._REPROCESS_JOB_NAME, status="success")
        except Exception as error:
            error_code = "REPROCESS_UNEXPECTED_ERROR"
            if isinstance(error, FlexRequestError):
                error_code = "REPROCESS_REQUEST_ERROR"
            elif isinstance(error, FlexStatementError):
                error_code = "REPROCESS_STATEMENT_ERROR"
            elif isinstance(error, FlexAdapterTimeoutError):
                error_code = "REPROCESS_TIMEOUT_ERROR"
            elif isinstance(error, FlexAdapterConnectionError):
                error_code = "REPROCESS_CONNECTION_ERROR"
            elif isinstance(error, TimeoutError):
                error_code = "REPROCESS_TIMEOUT_ERROR"
            elif isinstance(error, ConnectionError):
                error_code = "REPROCESS_CONNECTION_ERROR"
            elif isinstance(error, ValueError):
                error_code = "REPROCESS_CONTRACT_ERROR"

            timeline.append(
                domain_build_stage_event(
                    stage="run",
                    status="failed",
                    details={
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                        "traceback": traceback.format_exc(),
                        "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                    },
                )
            )
            ingestion_repository = self._ingestion_repository
            if run_record is not None and ingestion_repository is not None:
                ingestion_repository.db_ingestion_run_finalize(
                    ingestion_run_id=run_record.ingestion_run_id,
                    status="failed",
                    error_code=error_code,
                    error_message=str(error),
                    diagnostics=timeline,
                )
            return JobExecutionResult(job_name=self._REPROCESS_JOB_NAME, status="failed")

    def _job_reprocess_validate_period_key(self, period_key: str) -> str:
        """Validate explicit replay period key format.

        Args:
            period_key: Candidate replay period key.

        Returns:
            str: Validated `YYYY-MM-DD` period key.

        Raises:
            ValueError: Raised when period key is blank or has invalid format.
        """

        if not isinstance(period_key, str):
            raise ValueError("period_key must be a string")
        normalized_period_key = period_key.strip()
        if not normalized_period_key:
            raise ValueError("period_key must not be blank")
        try:
            datetime.strptime(normalized_period_key, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError("period_key must use YYYY-MM-DD format") from error
        return normalized_period_key
