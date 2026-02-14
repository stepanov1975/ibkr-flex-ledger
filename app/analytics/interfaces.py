"""Typed interfaces for analytics-layer aggregations."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class AnalyticsSummary:
    """Aggregated analytics summary for a grouping dimension.

    Attributes:
        grouping_key: Group identifier such as label or instrument.
        total_realized_pnl: Aggregated realized PnL value.
        total_unrealized_pnl: Aggregated unrealized PnL value.
    """

    grouping_key: str
    total_realized_pnl: Decimal
    total_unrealized_pnl: Decimal


class AnalyticsPort(Protocol):
    """Port definition for reporting and analytics aggregation services."""

    def analytics_dimension_name(self) -> str:
        """Return the primary aggregation dimension used by this service.

        Returns:
            str: Aggregation dimension identifier.

        Raises:
            RuntimeError: Raised when dimension metadata is unavailable.
        """

    def analytics_summarize_group(self, grouping_key: str) -> AnalyticsSummary:
        """Aggregate PnL metrics for one logical group.

        Args:
            grouping_key: Group key for report aggregation.

        Returns:
            AnalyticsSummary: Aggregated report values.

        Raises:
            ValueError: Raised when group cannot be resolved.
        """
