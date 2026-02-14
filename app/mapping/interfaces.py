"""Typed interfaces for mapping-layer transformations."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CanonicalEventRecord:
    """Canonical event output contract from mapping operations.

    Attributes:
        event_type: Canonical event type label.
        source_record_id: Identifier of originating raw record.
    """

    event_type: str
    source_record_id: str


class MappingPort(Protocol):
    """Port definition for mapping raw rows to canonical event records."""

    def mapping_contract_version(self) -> str:
        """Return mapping contract version used for canonical transforms.

        Returns:
            str: Mapping contract version identifier.

        Raises:
            RuntimeError: Raised when version metadata cannot be resolved.
        """

    def mapping_map_raw_record(self, raw_record_id: str) -> CanonicalEventRecord:
        """Map a raw record into a canonical event contract.

        Args:
            raw_record_id: Persistent raw record identifier.

        Returns:
            CanonicalEventRecord: Canonical event mapping output.

        Raises:
            ValueError: Raised when required source fields are missing.
        """
