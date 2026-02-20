"""Task 7 daily snapshot assembly and persistence service."""
# pylint: disable=too-few-public-methods

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from app.db import (
    LedgerCashflowRecord,
    LedgerSnapshotRepositoryPort,
    LedgerTradeFillRecord,
    PnlSnapshotDailyUpsertRequest,
    PositionLotUpsertRequest,
)

from .fifo_engine import FifoLedgerComputationRequest, FifoTradeFillInput, fifo_compute_instrument
from .snapshot_dates import snapshot_resolve_report_date_local


@dataclass(frozen=True)
class SnapshotBuildResult:
    """Result payload for Task 7 snapshot build workflow.

    Attributes:
        report_date_local: Local report date used for snapshot rows.
        snapshot_row_count: Number of daily snapshot rows persisted.
        position_lot_row_count: Number of position-lot rows persisted.
    """

    report_date_local: str
    snapshot_row_count: int
    position_lot_row_count: int


class StockLedgerSnapshotService:
    """Build and persist Task 7 daily snapshots from canonical events."""

    def __init__(self, repository: LedgerSnapshotRepositoryPort):
        """Initialize snapshot service dependencies.

        Args:
            repository: DB-layer ledger/snapshot repository.

        Returns:
            None: Initializer does not return values.

        Raises:
            ValueError: Raised when repository is invalid.
        """

        if repository is None:
            raise ValueError("repository must not be None")
        self._repository = repository

    def ledger_snapshot_build_and_persist(
        self,
        account_id: str,
        ingestion_run_id: str | None,
        run_completed_at_utc: str,
    ) -> SnapshotBuildResult:
        """Build and persist day-level snapshots for one account context.

        Args:
            account_id: Internal account identifier.
            ingestion_run_id: Optional ingestion run identifier.
            run_completed_at_utc: Offset-aware UTC timestamp for report-date resolution.

        Returns:
            SnapshotBuildResult: Persistence summary for this snapshot build run.

        Raises:
            ValueError: Raised when input values are invalid.
            RuntimeError: Raised when required canonical trade data is unavailable.
        """

        normalized_account_id = account_id.strip()
        if not normalized_account_id:
            raise ValueError("account_id must not be blank")

        report_date_local = snapshot_resolve_report_date_local(run_completed_at_utc)

        trade_rows = self._repository.db_ledger_trade_fill_list_for_account(
            account_id=normalized_account_id,
            through_report_date_local=report_date_local,
        )
        cashflow_rows = self._repository.db_ledger_cashflow_list_for_account(
            account_id=normalized_account_id,
            through_report_date_local=report_date_local,
        )

        trades_by_instrument = self._group_trades_by_instrument(trade_rows)
        cashflows_by_instrument = self._group_cashflows_by_instrument(cashflow_rows)

        snapshot_requests: list[PnlSnapshotDailyUpsertRequest] = []
        position_lot_requests: list[PositionLotUpsertRequest] = []

        for instrument_id, instrument_trades in trades_by_instrument.items():
            last_trade_price = Decimal(instrument_trades[-1].price)
            fifo_request = FifoLedgerComputationRequest(
                account_id=normalized_account_id,
                instrument_id=instrument_id,
                functional_currency=instrument_trades[-1].functional_currency,
                mark_price=last_trade_price,
                trades=[
                    FifoTradeFillInput(
                        event_trade_fill_id=str(trade.event_trade_fill_id),
                        source_raw_record_id=str(trade.source_raw_record_id),
                        trade_timestamp_utc=trade.trade_timestamp_utc.isoformat(),
                        side=trade.side,
                        quantity=Decimal(trade.quantity),
                        price=Decimal(trade.price),
                        fees=self._trade_fee_total(trade),
                        withholding_tax=Decimal("0"),
                    )
                    for trade in instrument_trades
                ],
            )
            fifo_result = fifo_compute_instrument(fifo_request)

            instrument_cashflows = cashflows_by_instrument.get(instrument_id, [])
            cashflow_fees_total = sum((Decimal(cashflow.fees or "0") for cashflow in instrument_cashflows), Decimal("0"))
            withholding_tax_total = sum(
                (Decimal(cashflow.withholding_tax or "0") for cashflow in instrument_cashflows),
                Decimal("0"),
            )

            trade_fee_total = sum((self._trade_fee_total(trade) for trade in instrument_trades), Decimal("0"))
            total_fee_impact = trade_fee_total + cashflow_fees_total
            realized_pnl = fifo_result.realized_pnl - cashflow_fees_total - withholding_tax_total
            unrealized_pnl = fifo_result.unrealized_pnl
            total_pnl = realized_pnl + unrealized_pnl

            snapshot_requests.append(
                PnlSnapshotDailyUpsertRequest(
                    account_id=normalized_account_id,
                    report_date_local=report_date_local,
                    instrument_id=instrument_id,
                    position_qty=str(fifo_result.position_quantity),
                    cost_basis=self._build_open_cost_basis(fifo_result.open_lots),
                    realized_pnl=str(realized_pnl),
                    unrealized_pnl=str(unrealized_pnl),
                    total_pnl=str(total_pnl),
                    fees=str(total_fee_impact),
                    withholding_tax=str(withholding_tax_total),
                    currency=instrument_trades[-1].functional_currency,
                    provisional=False,
                    valuation_source="trade_price_fallback",
                    fx_source="event_fx_fallback",
                    ingestion_run_id=ingestion_run_id,
                )
            )

            for open_lot in fifo_result.open_lots:
                position_lot_requests.append(
                    PositionLotUpsertRequest(
                        position_lot_id=self._build_position_lot_id(
                            account_id=normalized_account_id,
                            instrument_id=instrument_id,
                            open_event_trade_fill_id=open_lot.open_event_trade_fill_id,
                        ),
                        account_id=normalized_account_id,
                        instrument_id=instrument_id,
                        open_event_trade_fill_id=open_lot.open_event_trade_fill_id,
                        opened_at_utc=datetime.fromisoformat(open_lot.opened_at_utc),
                        closed_at_utc=None,
                        open_quantity=str(abs(open_lot.open_quantity)),
                        remaining_quantity=str(abs(open_lot.remaining_quantity)),
                        open_price=str(open_lot.open_price),
                        cost_basis_open=str(open_lot.cost_basis_open),
                        realized_pnl_to_date=str(open_lot.realized_pnl_to_date),
                        status="open",
                    )
                )

        self._repository.db_position_lot_upsert_many(position_lot_requests)
        self._repository.db_pnl_snapshot_daily_upsert_many(snapshot_requests)

        return SnapshotBuildResult(
            report_date_local=report_date_local,
            snapshot_row_count=len(snapshot_requests),
            position_lot_row_count=len(position_lot_requests),
        )

    def _group_trades_by_instrument(self, trade_rows: list[LedgerTradeFillRecord]) -> dict[str, list[LedgerTradeFillRecord]]:
        """Group trade-fill rows by instrument identifier.

        Args:
            trade_rows: Trade-fill rows.

        Returns:
            dict[str, list[LedgerTradeFillRecord]]: Grouped rows keyed by instrument identifier.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        grouped_rows: dict[str, list[LedgerTradeFillRecord]] = {}
        for trade_row in trade_rows:
            instrument_key = str(trade_row.instrument_id)
            grouped_rows.setdefault(instrument_key, []).append(trade_row)
        return grouped_rows

    def _group_cashflows_by_instrument(
        self,
        cashflow_rows: list[LedgerCashflowRecord],
    ) -> dict[str, list[LedgerCashflowRecord]]:
        """Group cashflow rows by instrument identifier.

        Args:
            cashflow_rows: Cashflow rows.

        Returns:
            dict[str, list[LedgerCashflowRecord]]: Grouped rows keyed by instrument identifier.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        grouped_rows: dict[str, list[LedgerCashflowRecord]] = {}
        for cashflow_row in cashflow_rows:
            if cashflow_row.instrument_id is None:
                continue
            instrument_key = str(cashflow_row.instrument_id)
            grouped_rows.setdefault(instrument_key, []).append(cashflow_row)
        return grouped_rows

    def _trade_fee_total(self, trade_row: LedgerTradeFillRecord) -> Decimal:
        """Build combined trade-fee impact from fees and commission fields.

        Args:
            trade_row: Trade-fill row.

        Returns:
            Decimal: Combined fee impact.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        fees = Decimal(trade_row.fees or "0")
        commission = Decimal(trade_row.commission or "0")
        return fees + commission

    def _build_open_cost_basis(self, open_lots) -> str | None:
        """Build open cost-basis aggregate from FIFO open lots.

        Args:
            open_lots: Open-lot result rows.

        Returns:
            str | None: Open cost-basis sum or None when no open position.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        if not open_lots:
            return None

        open_cost_basis = sum(
            (
                (lot.cost_basis_open / lot.open_quantity) * lot.remaining_quantity
                for lot in open_lots
                if lot.open_quantity > Decimal("0")
            ),
            Decimal("0"),
        )
        return str(open_cost_basis)

    def _build_position_lot_id(self, account_id: str, instrument_id: str, open_event_trade_fill_id: str) -> str:
        """Build deterministic position-lot identifier for idempotent upsert.

        Args:
            account_id: Internal account identifier.
            instrument_id: Canonical instrument identifier.
            open_event_trade_fill_id: Opening trade-fill identifier.

        Returns:
            str: Deterministic position-lot UUID string.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        lot_identity = f"{account_id}:{instrument_id}:{open_event_trade_fill_id}"
        return str(uuid5(NAMESPACE_URL, lot_identity))


__all__ = ["SnapshotBuildResult", "StockLedgerSnapshotService"]
