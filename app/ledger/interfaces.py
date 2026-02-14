"""Typed interfaces for ledger-layer computations."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class LedgerComputationResult:
    """Ledger computation output contract.

    Attributes:
        instrument_id: Internal instrument identifier.
        position_quantity: Open quantity at computation boundary.
        realized_pnl: Realized profit and loss value.
        unrealized_pnl: Unrealized profit and loss value.
    """

    instrument_id: str
    position_quantity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal


class LedgerPort(Protocol):
    """Port definition for position and PnL computations."""

    def ledger_policy_name(self) -> str:
        """Return policy label for the active ledger computation strategy.

        Returns:
            str: Ledger policy identifier.

        Raises:
            RuntimeError: Raised when policy metadata is unavailable.
        """

    def ledger_calculate_instrument(self, instrument_id: str) -> LedgerComputationResult:
        """Compute position and PnL for one instrument.

        Args:
            instrument_id: Internal instrument identifier.

        Returns:
            LedgerComputationResult: Deterministic computation output.

        Raises:
            ValueError: Raised when required canonical events are unavailable.
        """
