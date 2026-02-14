"""Typed interfaces for adapter-layer responsibilities."""

from dataclasses import dataclass
from typing import Any
from typing import Protocol


@dataclass(frozen=True)
class AdapterFetchResult:
    """Result contract for adapter fetch operations.

    Attributes:
        run_reference: Unique source run reference from upstream provider.
        payload_bytes: Immutable raw payload bytes.
        stage_timeline: Structured stage timeline entries captured by adapter.
    """

    run_reference: str
    payload_bytes: bytes
    stage_timeline: list[dict[str, Any]]


class FlexAdapterPort(Protocol):
    """Port definition for fetching raw Flex payloads from IBKR."""

    def adapter_source_name(self) -> str:
        """Return adapter source identifier for diagnostics and telemetry.

        Returns:
            str: Human-readable upstream source identifier.

        Raises:
            RuntimeError: Raised when source metadata is unavailable.
        """

    def adapter_fetch_report(self, query_id: str) -> AdapterFetchResult:
        """Fetch one report payload from the configured upstream source.

        Args:
            query_id: Upstream report identifier.

        Returns:
            AdapterFetchResult: Immutable fetch result payload contract.

        Raises:
            ConnectionError: Raised when upstream connection fails.
            TimeoutError: Raised when request exceeds timeout.
        """
