"""Typed interfaces for job-layer orchestration responsibilities."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class JobExecutionResult:
    """Result contract for long-running workflow execution.

    Attributes:
        job_name: Job identifier.
        status: Final execution state.
    """

    job_name: str
    status: str


class JobOrchestratorPort(Protocol):
    """Port definition for orchestrating ingestion and recompute jobs."""

    def job_supported_names(self) -> tuple[str, ...]:
        """Return the set of workflow names this orchestrator can execute.

        Returns:
            tuple[str, ...]: Deterministic list of supported job names.

        Raises:
            RuntimeError: Raised when supported job metadata is unavailable.
        """

    def job_execute(self, job_name: str) -> JobExecutionResult:
        """Execute one named workflow in the job layer.

        Args:
            job_name: Workflow name.

        Returns:
            JobExecutionResult: Final execution status payload.

        Raises:
            RuntimeError: Raised when job execution fails.
        """
