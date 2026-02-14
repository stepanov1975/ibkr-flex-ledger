"""Job-layer orchestrator for deterministic canonical reprocess workflows."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from app.db import CanonicalPersistenceRepositoryPort, IngestionRunRepositoryPort, RawRecordReadRepositoryPort
from app.domain import domain_build_stage_event

from .canonical_pipeline import job_canonical_map_and_persist
from .interfaces import JobExecutionResult, JobOrchestratorPort


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
        config: CanonicalReprocessOrchestratorConfig,
        ingestion_repository: IngestionRunRepositoryPort | None = None,
    ):
        """Initialize canonical reprocess dependencies.

        Args:
            raw_read_repository: Raw-row read repository.
            canonical_persistence_repository: Canonical persistence repository.
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

        return self._job_reprocess_execute_with_config(config=self._config)

    def job_execute_reprocess_target(self, period_key: str, flex_query_id: str) -> JobExecutionResult:
        """Execute canonical reprocess for one explicit period/query target.

        Args:
            period_key: Ingestion period identity key for replay scope.
            flex_query_id: Upstream Flex query identifier for replay scope.

        Returns:
            JobExecutionResult: Final execution status payload.

        Raises:
            ValueError: Raised when explicit scope values are invalid.
            RuntimeError: Raised for unexpected execution failures after finalization.
        """

        normalized_period_key = self._job_reprocess_validate_period_key(period_key)
        if not isinstance(flex_query_id, str):
            raise ValueError("flex_query_id must be a string")
        normalized_flex_query_id = flex_query_id.strip()
        if not normalized_flex_query_id:
            raise ValueError("flex_query_id must not be blank")
        scoped_config = replace(
            self._config,
            period_key=normalized_period_key,
            flex_query_id=normalized_flex_query_id,
        )
        return self._job_reprocess_execute_with_config(config=scoped_config)

    def _job_reprocess_execute_with_config(self, config: CanonicalReprocessOrchestratorConfig) -> JobExecutionResult:
        """Execute canonical reprocess using the provided replay scope config.

        Args:
            config: Effective replay scope values.

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
            raw_rows = self._raw_read_repository.db_raw_record_list_for_period(
                account_id=config.account_id,
                period_key=config.period_key,
                flex_query_id=config.flex_query_id,
            )
            timeline.append(
                domain_build_stage_event(
                    stage="raw_read",
                    status="completed",
                    details={"raw_row_count": len(raw_rows)},
                )
            )

            timeline.append(domain_build_stage_event(stage="canonical_mapping", status="started"))
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
                        **canonical_counts,
                        "canonical_duration_ms": canonical_duration_ms,
                    },
                )
            )

            timeline.append(domain_build_stage_event(stage="run", status="success"))
            if run_record is not None:
                self._ingestion_repository.db_ingestion_run_finalize(
                    ingestion_run_id=run_record.ingestion_run_id,
                    status="success",
                    error_code=None,
                    error_message=None,
                    diagnostics=timeline,
                )
            return JobExecutionResult(job_name=self._REPROCESS_JOB_NAME, status="success")
        except Exception as error:
            timeline.append(
                domain_build_stage_event(
                    stage="run",
                    status="failed",
                    details={
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                        "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                    },
                )
            )
            if run_record is not None:
                self._ingestion_repository.db_ingestion_run_finalize(
                    ingestion_run_id=run_record.ingestion_run_id,
                    status="failed",
                    error_code="REPROCESS_UNEXPECTED_ERROR",
                    error_message=str(error),
                    diagnostics=timeline,
                )
            raise RuntimeError("canonical reprocess execution failed") from error

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
