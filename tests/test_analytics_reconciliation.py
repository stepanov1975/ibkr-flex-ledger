"""Frozen reconciliation tolerance boundary tests."""

from datetime import date
from uuid import uuid4

from app.analytics import analytics_build_reconciliation_diffs
from app.db import ReconciliationSourceRecord


def _source(realized: str, broker_realized: str | None) -> ReconciliationSourceRecord:
    return ReconciliationSourceRecord(
        report_date_local=date(2026, 8, 21), instrument_id=uuid4(), conid="123", symbol="TEST", currency="USD",
        position_qty="1", realized_pnl=realized, unrealized_pnl="0", fees="0", withholding_tax="0",
        broker_position_qty="1", broker_realized_pnl=broker_realized, broker_unrealized_pnl="0",
        broker_fees="0", broker_withholding_tax="0", source_event_id=None, source_raw_record_id=None,
        provisional=False,
    )


def test_reconciliation_passes_at_absolute_tolerance_boundary() -> None:
    """Treat an exact one-cent USD difference as within tolerance."""

    diff = analytics_build_reconciliation_diffs([_source("10.00", "10.01")])[1]
    assert diff.metric == "realized_pnl"
    assert diff.within_tolerance is True


def test_reconciliation_fails_when_thresholds_are_exceeded() -> None:
    """Flag a difference exceeding both absolute and relative limits."""

    diff = analytics_build_reconciliation_diffs([_source("10.00", "10.02")])[1]
    assert diff.within_tolerance is False


def test_missing_broker_value_is_provisional_mismatch() -> None:
    """Never hide a missing broker comparison value."""

    diff = analytics_build_reconciliation_diffs([_source("10.00", None)])[1]
    assert diff.within_tolerance is False
    assert diff.provisional is True
    assert diff.rel_diff is None
