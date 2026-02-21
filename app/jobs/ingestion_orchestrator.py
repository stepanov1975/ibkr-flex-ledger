"""Job-layer ingestion orchestrator with deterministic stage timeline persistence."""
# pylint: disable=duplicate-code

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import traceback

from app.adapters import (
    FlexAdapterConnectionError,
    FlexAdapterPort,
    FlexAdapterTimeoutError,
    FlexRequestError,
    FlexStatementError,
    FlexTokenExpiredError,
    FlexTokenInvalidError,
)
from app.db import (
    CanonicalPersistenceRepositoryPort,
    IngestionRunRepositoryPort,
    RawArtifactPersistRequest,
    RawArtifactReference,
    RawRecordReadRepositoryPort,
    RawPersistenceRepositoryPort,
    RawRecordPersistRequest,
)
from app.domain import domain_build_stage_event
from app.ledger import StockLedgerSnapshotService

from .interfaces import JobExecutionResult, JobOrchestratorPort
from .raw_extraction import job_raw_extract_payload_rows
from .canonical_pipeline import job_canonical_map_and_persist
from .section_preflight import (
    MISSING_REQUIRED_SECTION_CODE,
    job_section_preflight_build_missing_required_diagnostics,
    job_section_preflight_validate_required_sections,
)


@dataclass(frozen=True)
class IngestionOrchestratorConfig:
    """Configuration values for ingestion orchestration execution.

    Attributes:
        account_id: Internal single-account context identifier.
        flex_query_id: Upstream Flex query identifier.
        run_type: Run source type (`scheduled`, `manual`, `reprocess`).
        reconciliation_enabled: Whether reconciliation-required section checks are enforced.
        functional_currency: Functional/base reporting currency code.
    """

    account_id: str
    flex_query_id: str
    run_type: str = "manual"
    reconciliation_enabled: bool = False
    functional_currency: str = "USD"


class IngestionJobOrchestrator(JobOrchestratorPort):
    """Concrete job orchestrator for Task 3 ingestion workflow."""

    _INGESTION_JOB_NAME = "ingestion_run"

    def __init__(
        self,
        ingestion_repository: IngestionRunRepositoryPort,
        raw_persistence_repository: RawPersistenceRepositoryPort,
        flex_adapter: FlexAdapterPort,
        config: IngestionOrchestratorConfig,
        canonical_repository: CanonicalPersistenceRepositoryPort | RawRecordReadRepositoryPort | None = None,
        snapshot_service: StockLedgerSnapshotService | None = None,
    ):
        """Initialize ingestion orchestrator dependencies.

        Args:
            ingestion_repository: DB-layer ingestion run persistence service.
            raw_persistence_repository: DB-layer immutable raw persistence service.
            flex_adapter: Adapter for upstream Flex retrieval.
            config: Ingestion execution configuration.

        Returns:
            None: Initializer does not return a value.

        Raises:
            ValueError: Raised when dependencies or config values are invalid.
        """

        if ingestion_repository is None:
            raise ValueError("ingestion_repository must not be None")
        if raw_persistence_repository is None:
            raise ValueError("raw_persistence_repository must not be None")
        if flex_adapter is None:
            raise ValueError("flex_adapter must not be None")
        if not config.account_id.strip():
            raise ValueError("config.account_id must not be blank")
        if not config.flex_query_id.strip():
            raise ValueError("config.flex_query_id must not be blank")
        if not config.run_type.strip():
            raise ValueError("config.run_type must not be blank")
        if not config.functional_currency.strip():
            raise ValueError("config.functional_currency must not be blank")

        self._ingestion_repository = ingestion_repository
        self._raw_persistence_repository = raw_persistence_repository
        self._flex_adapter = flex_adapter
        self._config = config
        self._canonical_repository = canonical_repository
        self._snapshot_service = snapshot_service

    def job_supported_names(self) -> tuple[str, ...]:
        """Return supported job names.

        Returns:
            tuple[str, ...]: Supported job names.

        Raises:
            RuntimeError: This implementation does not raise runtime errors.
        """

        return (self._INGESTION_JOB_NAME,)

    def job_execute(self, job_name: str) -> JobExecutionResult:
        """Execute ingestion workflow with deterministic stage timeline persistence.

        Args:
            job_name: Name of job to execute.

        Returns:
            JobExecutionResult: Final execution status payload.

        Raises:
            ValueError: Raised when job name is unsupported.
            RuntimeError: Raised for unexpected execution failures after finalization.
        """

        normalized_job_name = job_name.strip()
        if normalized_job_name != self._INGESTION_JOB_NAME:
            raise ValueError(f"unsupported job_name={normalized_job_name}")

        period_key = datetime.now(timezone.utc).date().isoformat()
        timeline: list[dict[str, object]] = []
        timeline.append(domain_build_stage_event(stage="run", status="started"))

        run_record = self._ingestion_repository.db_ingestion_run_create_started(
            account_id=self._config.account_id,
            run_type=self._config.run_type,
            period_key=period_key,
            flex_query_id=self._config.flex_query_id,
            report_date_local=None,
        )

        try:
            adapter_result = self._flex_adapter.adapter_fetch_report(query_id=self._config.flex_query_id)
            timeline.extend(adapter_result.stage_timeline)

            timeline.append(domain_build_stage_event(stage="preflight", status="started"))
            preflight_result = job_section_preflight_validate_required_sections(
                payload_bytes=adapter_result.payload_bytes,
                reconciliation_enabled=self._config.reconciliation_enabled,
            )

            if not preflight_result.section_preflight_is_valid():
                return self._job_handle_preflight_failure(
                    run_record=run_record,
                    preflight_result=preflight_result,
                    timeline=timeline,
                    normalized_job_name=normalized_job_name,
                )

            timeline.append(
                domain_build_stage_event(
                    stage="preflight",
                    status="completed",
                    details={"detected_sections": list(preflight_result.detected_sections)},
                )
            )

            timeline.append(domain_build_stage_event(stage="persist", status="started"))
            payload_sha256 = hashlib.sha256(adapter_result.payload_bytes).hexdigest()
            extraction_result = job_raw_extract_payload_rows(payload_bytes=adapter_result.payload_bytes)

            artifact_result = self._raw_persistence_repository.db_raw_artifact_upsert(
                request=RawArtifactPersistRequest(
                    ingestion_run_id=run_record.ingestion_run_id,
                    reference=RawArtifactReference(
                        account_id=self._config.account_id,
                        period_key=period_key,
                        flex_query_id=self._config.flex_query_id,
                        payload_sha256=payload_sha256,
                        report_date_local=extraction_result.report_date_local,
                    ),
                    source_payload=adapter_result.payload_bytes,
                )
            )

            raw_record_requests = [
                RawRecordPersistRequest(
                    ingestion_run_id=run_record.ingestion_run_id,
                    raw_artifact_id=artifact_result.artifact.raw_artifact_id,
                    artifact_reference=artifact_result.artifact.reference,
                    report_date_local=extraction_result.report_date_local,
                    section_name=extracted_row.section_name,
                    source_row_ref=extracted_row.source_row_ref,
                    source_payload=extracted_row.source_payload,
                )
                for extracted_row in extraction_result.rows
            ]
            raw_record_result = self._raw_persistence_repository.db_raw_record_insert_many(raw_record_requests)

            timeline.append(
                domain_build_stage_event(
                    stage="persist",
                    status="completed",
                    details={
                        "payload_sha256": payload_sha256,
                        "raw_artifact_id": str(artifact_result.artifact.raw_artifact_id),
                        "raw_artifact_deduplicated": artifact_result.deduplicated,
                        "raw_record_count": raw_record_result.inserted_count,
                        "raw_record_deduplicated_count": raw_record_result.deduplicated_count,
                    },
                )
            )

            if self._canonical_repository is not None:
                timeline.append(domain_build_stage_event(stage="canonical_mapping", status="started"))
                canonical_raw_rows = self._canonical_repository.db_raw_record_list_for_run(
                    ingestion_run_id=run_record.ingestion_run_id,
                )
                canonical_started_at = datetime.now(timezone.utc)
                if len(canonical_raw_rows) == 0:
                    canonical_counts = {
                        "instrument_upsert_count": 0,
                        "trade_fill_count": 0,
                        "cashflow_count": 0,
                        "fx_count": 0,
                        "corp_action_count": 0,
                    }
                    canonical_skip_reason = "no_new_raw_rows_for_run"
                else:
                    canonical_counts = job_canonical_map_and_persist(
                        account_id=self._config.account_id,
                        functional_currency=self._config.functional_currency,
                        raw_records=canonical_raw_rows,
                        canonical_persistence_repository=self._canonical_repository,
                    )
                    canonical_skip_reason = None
                canonical_duration_ms = max(
                    0,
                    int((datetime.now(timezone.utc) - canonical_started_at).total_seconds() * 1000),
                )
                canonical_details: dict[str, object] = {
                    **canonical_counts,
                    "canonical_input_row_count": len(canonical_raw_rows),
                    "canonical_duration_ms": canonical_duration_ms,
                }
                if canonical_skip_reason is not None:
                    canonical_details["canonical_skip_reason"] = canonical_skip_reason
                timeline.append(
                    domain_build_stage_event(
                        stage="canonical_mapping",
                        status="completed",
                        details=canonical_details,
                    )
                )

            self._job_append_snapshot_stage_timeline(
                run_record_id=str(run_record.ingestion_run_id),
                timeline=timeline,
            )

            timeline.append(domain_build_stage_event(stage="run", status="success"))
            self._ingestion_repository.db_ingestion_run_finalize(
                ingestion_run_id=run_record.ingestion_run_id,
                status="success",
                error_code=None,
                error_message=None,
                diagnostics=timeline,
            )
            return JobExecutionResult(job_name=normalized_job_name, status="success")
        except (TimeoutError, ConnectionError, ValueError, RuntimeError) as error:
            error_code = self._job_error_code_for_exception(error)

            timeline.append(
                domain_build_stage_event(
                    stage="run",
                    status="failed",
                    details={
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                        "traceback": traceback.format_exc(),
                    },
                )
            )
            self._ingestion_repository.db_ingestion_run_finalize(
                ingestion_run_id=run_record.ingestion_run_id,
                status="failed",
                error_code=error_code,
                error_message=str(error),
                diagnostics=timeline,
            )
            return JobExecutionResult(job_name=normalized_job_name, status="failed")

    def _job_handle_preflight_failure(
        self,
        run_record,
        preflight_result,
        timeline: list[dict[str, object]],
        normalized_job_name: str,
    ) -> JobExecutionResult:
        """Finalize run with deterministic preflight-missing-section failure payload.

        Args:
            run_record: Started ingestion run record.
            preflight_result: Required-section preflight result.
            timeline: Mutable stage timeline events.
            normalized_job_name: Validated job name.

        Returns:
            JobExecutionResult: Failed execution result.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        missing_diagnostics = job_section_preflight_build_missing_required_diagnostics(preflight_result)
        timeline.append(
            domain_build_stage_event(
                stage="preflight",
                status="failed",
                details={
                    "error_code": MISSING_REQUIRED_SECTION_CODE,
                    "missing_hard_required": list(preflight_result.missing_hard_required),
                    "missing_reconciliation_required": list(preflight_result.missing_reconciliation_required),
                },
            )
        )
        timeline.extend(missing_diagnostics)
        timeline.append(domain_build_stage_event(stage="run", status="failed"))
        self._ingestion_repository.db_ingestion_run_finalize(
            ingestion_run_id=run_record.ingestion_run_id,
            status="failed",
            error_code=MISSING_REQUIRED_SECTION_CODE,
            error_message="missing required sections",
            diagnostics=timeline,
        )
        return JobExecutionResult(job_name=normalized_job_name, status="failed")

    def _job_error_code_for_exception(self, error: Exception) -> str:
        """Map runtime exception type to deterministic ingestion failure code.

        Args:
            error: Caught workflow exception.

        Returns:
            str: Deterministic error code.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        if isinstance(error, FlexTokenExpiredError):
            return "INGESTION_TOKEN_EXPIRED_ERROR"
        if isinstance(error, FlexTokenInvalidError):
            return "INGESTION_TOKEN_INVALID_ERROR"
        if isinstance(error, FlexRequestError):
            return "INGESTION_REQUEST_ERROR"
        if isinstance(error, FlexStatementError):
            return "INGESTION_STATEMENT_ERROR"
        if isinstance(error, FlexAdapterTimeoutError):
            return "INGESTION_TIMEOUT_ERROR"
        if isinstance(error, FlexAdapterConnectionError):
            return "INGESTION_CONNECTION_ERROR"
        if isinstance(error, TimeoutError):
            return "INGESTION_TIMEOUT_ERROR"
        if isinstance(error, ConnectionError):
            return "INGESTION_CONNECTION_ERROR"
        if isinstance(error, ValueError):
            return "INGESTION_CONTRACT_ERROR"
        return "INGESTION_UNEXPECTED_ERROR"

    def _job_append_snapshot_stage_timeline(self, run_record_id: str, timeline: list[dict[str, object]]) -> None:
        """Append snapshot stage timeline events for automatic Task 7 execution.

        Args:
            run_record_id: Ingestion run identifier.
            timeline: Mutable timeline payload being persisted.

        Returns:
            None: Timeline is updated as side effect.

        Raises:
            RuntimeError: Raised when snapshot service execution fails.
        """

        timeline.append(domain_build_stage_event(stage="snapshot", status="started"))
        snapshot_started_at = datetime.now(timezone.utc)
        if self._snapshot_service is None:
            timeline.append(
                domain_build_stage_event(
                    stage="snapshot",
                    status="completed",
                    details={"snapshot_skip_reason": "snapshot_service_not_configured"},
                )
            )
            return

        snapshot_result = self._snapshot_service.ledger_snapshot_build_and_persist(
            account_id=self._config.account_id,
            ingestion_run_id=run_record_id,
            run_completed_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        snapshot_duration_ms = max(
            0,
            int((datetime.now(timezone.utc) - snapshot_started_at).total_seconds() * 1000),
        )
        timeline.append(
            domain_build_stage_event(
                stage="snapshot",
                status="completed",
                details={
                    "report_date_local": snapshot_result.report_date_local,
                    "snapshot_row_count": snapshot_result.snapshot_row_count,
                    "position_lot_row_count": snapshot_result.position_lot_row_count,
                    "missing_solid_valuation_count": snapshot_result.missing_solid_valuation_count,
                    "snapshot_duration_ms": snapshot_duration_ms,
                },
            )
        )
