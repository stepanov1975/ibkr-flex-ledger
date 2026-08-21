"""Task 7 daily snapshot assembly and persistence service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from app.db import (
    LedgerCashflowRecord,
    LedgerCorporateActionRecord,
    LedgerFxRateRecord,
    LedgerOpenPositionValuationRecord,
    LedgerSnapshotRepositoryPort,
    LedgerTradeFillRecord,
    PnlSnapshotDailyUpsertRequest,
    PositionLotUpsertRequest,
)

from .fifo_engine import FifoLedgerComputationRequest, FifoOpenLotResult, FifoTradeFillInput, fifo_compute_instrument


@dataclass(frozen=True)
class SnapshotBuildResult:
    """Result payload for Task 7 snapshot build workflow.

    Attributes:
        report_date_local: Local report date used for snapshot rows.
        snapshot_row_count: Number of daily snapshot rows persisted.
        position_lot_row_count: Number of position-lot rows persisted.
        missing_solid_valuation_count: Number of rows marked missing due absent solid valuation.
    """

    report_date_local: str
    snapshot_row_count: int
    position_lot_row_count: int
    missing_solid_valuation_count: int


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
        report_date_local: str,
        affected_conids: frozenset[str] | None = None,
        affected_currencies: frozenset[str] | None = None,
    ) -> SnapshotBuildResult:
        """Build and persist day-level snapshots for one account context.

        Args:
            account_id: Internal account identifier.
            ingestion_run_id: Optional ingestion run identifier.
            report_date_local: Flex statement business date in YYYY-MM-DD format.

        Returns:
            SnapshotBuildResult: Persistence summary for this snapshot build run.

        Raises:
            ValueError: Raised when input values are invalid.
            RuntimeError: Raised when required canonical trade data is unavailable.
        """

        normalized_account_id = account_id.strip()
        if not normalized_account_id:
            raise ValueError("account_id must not be blank")

        try:
            parsed_report_date = date.fromisoformat(report_date_local)
        except (TypeError, ValueError) as error:
            raise ValueError("report_date_local must use YYYY-MM-DD format") from error
        normalized_report_date = parsed_report_date.isoformat()

        is_full_build = affected_conids is None and affected_currencies is None
        if not is_full_build and not affected_conids and not affected_currencies:
            return SnapshotBuildResult(
                report_date_local=normalized_report_date,
                snapshot_row_count=0,
                position_lot_row_count=0,
                missing_solid_valuation_count=0,
            )

        instrument_ids: tuple[str, ...] | None = None
        if not is_full_build:
            instrument_ids = tuple(
                self._repository.db_ledger_instrument_ids_for_scope(
                    account_id=normalized_account_id,
                    conids=tuple(sorted(affected_conids or ())),
                    currencies=tuple(sorted(affected_currencies or ())),
                )
            )
            if not instrument_ids:
                return SnapshotBuildResult(
                    report_date_local=normalized_report_date,
                    snapshot_row_count=0,
                    position_lot_row_count=0,
                    missing_solid_valuation_count=0,
                )

        trade_rows = self._repository.db_ledger_trade_fill_list_for_account(
            account_id=normalized_account_id,
            through_report_date_local=normalized_report_date,
            instrument_ids=instrument_ids,
        )
        cashflow_rows = self._repository.db_ledger_cashflow_list_for_account(
            account_id=normalized_account_id,
            through_report_date_local=normalized_report_date,
            instrument_ids=instrument_ids,
        )

        fx_currencies: tuple[str, ...] | None = None
        if instrument_ids is not None:
            required_currencies = set(affected_currencies or ())
            required_currencies.update(self._repository.db_ledger_instrument_currency_list(instrument_ids))
            required_currencies.update(row.currency for row in trade_rows)
            required_currencies.update(row.functional_currency for row in trade_rows)
            required_currencies.update(row.currency for row in cashflow_rows)
            required_currencies.update(row.functional_currency for row in cashflow_rows)
            fx_currencies = tuple(sorted(required_currencies))

        fx_rate_rows = self._repository.db_ledger_fx_rate_list_for_account(
            account_id=normalized_account_id,
            through_report_date_local=normalized_report_date,
            currencies=fx_currencies,
        )
        corporate_action_reader = getattr(self._repository, "db_ledger_corporate_action_list_for_account", None)
        corporate_action_rows = (
            corporate_action_reader(
                account_id=normalized_account_id,
                through_report_date_local=normalized_report_date,
                instrument_ids=instrument_ids,
            )
            if corporate_action_reader is not None
            else []
        )
        open_position_valuation_map = self._build_open_position_valuation_map(
            self._repository.db_ledger_open_position_valuation_list_for_run(
                account_id=normalized_account_id,
                ingestion_run_id=ingestion_run_id or "00000000-0000-0000-0000-000000000000",
                instrument_ids=instrument_ids,
            )
            if ingestion_run_id is not None
            else []
        )

        trades_by_instrument = self._group_trades_by_instrument(trade_rows)
        cashflows_by_instrument = self._group_cashflows_by_instrument(cashflow_rows)
        corporate_actions_by_instrument = self._group_corporate_actions_by_instrument(corporate_action_rows)

        snapshot_requests: list[PnlSnapshotDailyUpsertRequest] = []
        position_lot_requests: list[PositionLotUpsertRequest] = []
        missing_solid_valuation_count = 0

        for instrument_id, instrument_trades in trades_by_instrument.items():
            functional_currency = instrument_trades[-1].functional_currency
            converted_trades: list[FifoTradeFillInput] = []
            fx_sources: set[str] = set()
            missing_fx = False
            for trade in instrument_trades:
                adjustment_factor = self._trade_adjustment_factor(
                    trade,
                    corporate_actions_by_instrument.get(instrument_id, []),
                )
                trade_fx_rate, trade_fx_source = self._resolve_fx_rate(
                    currency=trade.currency,
                    functional_currency=trade.functional_currency,
                    report_date_local=trade.report_date_local,
                    fx_rate_rows=fx_rate_rows,
                    trade=trade,
                )
                fx_sources.add(trade_fx_source)
                if trade_fx_rate is None:
                    missing_fx = True
                    trade_fx_rate = Decimal("0")
                converted_trades.append(
                    FifoTradeFillInput(
                        event_trade_fill_id=str(trade.event_trade_fill_id),
                        source_raw_record_id=str(trade.source_raw_record_id),
                        trade_timestamp_utc=trade.trade_timestamp_utc.isoformat(),
                        side=trade.side,
                        quantity=Decimal(trade.quantity) * adjustment_factor,
                        price=Decimal(trade.price) * trade_fx_rate / adjustment_factor,
                        fees=self._trade_fee_total(trade) * trade_fx_rate,
                        withholding_tax=Decimal("0"),
                    )
                )

            position_result = fifo_compute_instrument(
                FifoLedgerComputationRequest(
                    account_id=normalized_account_id,
                    instrument_id=instrument_id,
                    functional_currency=functional_currency,
                    mark_price=Decimal("0"),
                    trades=converted_trades,
                )
            )
            valuation_record = open_position_valuation_map.get(instrument_id)
            local_mark_price, valuation_source = self._resolve_mark_price(
                trades=instrument_trades,
                valuation_record=valuation_record,
                position_quantity=position_result.position_quantity,
                report_date_local=parsed_report_date,
            )
            missing_valuation = position_result.position_quantity != Decimal("0") and local_mark_price is None
            mark_price_base = Decimal("0")
            if local_mark_price is not None:
                mark_fx_rate, mark_fx_source = self._resolve_fx_rate(
                    currency=instrument_trades[-1].currency,
                    functional_currency=functional_currency,
                    report_date_local=parsed_report_date,
                    fx_rate_rows=fx_rate_rows,
                )
                fx_sources.add(mark_fx_source)
                if mark_fx_rate is None:
                    missing_fx = True
                else:
                    mark_price_base = local_mark_price * mark_fx_rate

            fifo_request = FifoLedgerComputationRequest(
                account_id=normalized_account_id,
                instrument_id=instrument_id,
                functional_currency=functional_currency,
                mark_price=mark_price_base,
                trades=converted_trades,
            )
            fifo_result = fifo_compute_instrument(fifo_request)

            instrument_cashflows = cashflows_by_instrument.get(instrument_id, [])
            cashflow_amount_total = Decimal("0")
            cashflow_fees_total = Decimal("0")
            withholding_tax_total = Decimal("0")
            for cashflow in instrument_cashflows:
                cashflow_fx_rate, cashflow_fx_source = self._resolve_fx_rate(
                    currency=cashflow.currency,
                    functional_currency=cashflow.functional_currency,
                    report_date_local=cashflow.report_date_local,
                    fx_rate_rows=fx_rate_rows,
                )
                if cashflow_fx_rate is None:
                    if cashflow.amount_in_base is None or Decimal(cashflow.fees or "0") != Decimal("0") or Decimal(
                        cashflow.withholding_tax or "0"
                    ) != Decimal("0"):
                        missing_fx = True
                    cashflow_fx_rate = Decimal("0")
                if cashflow.amount_in_base is not None:
                    cashflow_amount_total += Decimal(cashflow.amount_in_base)
                    fx_sources.add("cashflow_amount_in_base")
                else:
                    cashflow_amount_total += Decimal(cashflow.amount) * cashflow_fx_rate
                fx_sources.add(cashflow_fx_source)
                cashflow_fees_total += Decimal(cashflow.fees or "0") * cashflow_fx_rate
                withholding_tax_total += Decimal(cashflow.withholding_tax or "0") * cashflow_fx_rate

            trade_fee_total = sum((trade.fees or Decimal("0") for trade in converted_trades), Decimal("0"))
            total_fee_impact = trade_fee_total + cashflow_fees_total
            realized_pnl = fifo_result.realized_pnl + cashflow_amount_total - cashflow_fees_total - withholding_tax_total
            unrealized_pnl = fifo_result.unrealized_pnl
            total_pnl = realized_pnl + unrealized_pnl
            provisional = missing_valuation or missing_fx
            fx_source = ",".join(sorted(fx_sources)) if fx_sources else "base_currency"

            snapshot_requests.append(
                PnlSnapshotDailyUpsertRequest(
                    account_id=normalized_account_id,
                    report_date_local=normalized_report_date,
                    instrument_id=instrument_id,
                    position_qty=str(fifo_result.position_quantity),
                    cost_basis=self._build_open_cost_basis(fifo_result.open_lots),
                    realized_pnl=str(realized_pnl),
                    unrealized_pnl=str(unrealized_pnl),
                    total_pnl=str(total_pnl),
                    fees=str(total_fee_impact),
                    withholding_tax=str(withholding_tax_total),
                    currency=functional_currency,
                    provisional=provisional,
                    valuation_source=valuation_source,
                    fx_source=fx_source,
                    ingestion_run_id=ingestion_run_id,
                )
            )

            if missing_valuation:
                missing_solid_valuation_count += 1

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

        closed_at_utc = max(
            (trade.trade_timestamp_utc for trade in trade_rows),
            default=datetime.now(timezone.utc),
        )
        if instrument_ids is not None:
            selected_instrument_ids = set(instrument_ids)
            position_lot_requests = [
                request
                for request in position_lot_requests
                if request.instrument_id in selected_instrument_ids
            ]
            snapshot_requests = [
                request
                for request in snapshot_requests
                if request.instrument_id in selected_instrument_ids
            ]
        self._repository.db_position_lot_reconcile_open(
            account_id=normalized_account_id,
            closed_at_utc=closed_at_utc,
            requests=position_lot_requests,
            instrument_ids=instrument_ids,
        )
        self._repository.db_pnl_snapshot_daily_upsert_many(snapshot_requests)

        return SnapshotBuildResult(
            report_date_local=normalized_report_date,
            snapshot_row_count=len(snapshot_requests),
            position_lot_row_count=len(position_lot_requests),
            missing_solid_valuation_count=missing_solid_valuation_count,
        )

    def _build_open_position_valuation_map(
        self,
        rows: list[LedgerOpenPositionValuationRecord],
    ) -> dict[str, LedgerOpenPositionValuationRecord]:
        """Build instrument-keyed map for broker OpenPositions valuation rows.

        Args:
            rows: Broker OpenPositions valuation rows.

        Returns:
            dict[str, LedgerOpenPositionValuationRecord]: Valuation rows keyed by instrument id.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        return {str(row.instrument_id): row for row in rows}

    def _resolve_mark_price(
        self,
        trades: list[LedgerTradeFillRecord],
        valuation_record: LedgerOpenPositionValuationRecord | None,
        position_quantity: Decimal,
        report_date_local: date,
    ) -> tuple[Decimal | None, str]:
        """Resolve the frozen EOD mark hierarchy for one instrument."""

        if position_quantity == Decimal("0"):
            return Decimal("0"), "no_open_position"
        if valuation_record is not None and Decimal(valuation_record.position_qty) == position_quantity:
            mark_price = Decimal(valuation_record.mark_price)
            if mark_price > Decimal("0"):
                return mark_price, "openpositions_mark_price"

        same_day_close_prices = [
            Decimal(trade.close_price)
            for trade in trades
            if trade.report_date_local == report_date_local and trade.close_price is not None
        ]
        if same_day_close_prices:
            return same_day_close_prices[-1], "trades_close_price"

        eligible_trades = [trade for trade in trades if trade.report_date_local <= report_date_local]
        if eligible_trades:
            return Decimal(eligible_trades[-1].price), "trades_last_trade_price"
        return None, "EOD_MARK_MISSING_ALL_SOURCES"

    def _resolve_fx_rate(
        self,
        currency: str,
        functional_currency: str,
        report_date_local: date,
        fx_rate_rows: list[LedgerFxRateRecord],
        trade: LedgerTradeFillRecord | None = None,
    ) -> tuple[Decimal | None, str]:
        """Resolve the frozen execution/conversion FX fallback hierarchy."""

        normalized_currency = currency.strip().upper()
        normalized_functional_currency = functional_currency.strip().upper()
        if normalized_currency == normalized_functional_currency:
            return Decimal("1"), "base_currency"

        if trade is not None:
            direct_rate = self._positive_decimal_or_none(trade.fx_rate_to_base)
            if direct_rate is not None:
                return direct_rate, "trade_fx_rate_to_base"
            net_cash = self._decimal_or_none(trade.net_cash)
            net_cash_in_base = self._decimal_or_none(trade.net_cash_in_base)
            if net_cash is not None and net_cash != Decimal("0") and net_cash_in_base is not None:
                derived_rate = abs(net_cash_in_base) / abs(net_cash)
                if derived_rate > Decimal("0"):
                    return derived_rate, "trade_net_cash_ratio"

        candidates = [
            row
            for row in fx_rate_rows
            if row.currency.strip().upper() == normalized_currency
            and row.functional_currency.strip().upper() == normalized_functional_currency
            and row.report_date_local <= report_date_local
            and self._positive_decimal_or_none(row.fx_rate) is not None
        ]
        if candidates:
            selected = candidates[-1]
            selected_rate = self._positive_decimal_or_none(selected.fx_rate)
            if selected_rate is not None:
                date_label = "exact" if selected.report_date_local == report_date_local else "previous"
                return selected_rate, f"conversion_rates_{date_label}"
        return None, "FX_RATE_MISSING_ALL_SOURCES"

    def _positive_decimal_or_none(self, value: str | None) -> Decimal | None:
        """Return a strictly positive decimal or None for absent/non-positive values."""

        parsed_value = self._decimal_or_none(value)
        if parsed_value is None or parsed_value <= Decimal("0"):
            return None
        return parsed_value

    def _decimal_or_none(self, value: str | None) -> Decimal | None:
        """Parse an optional decimal string."""

        if value is None:
            return None
        return Decimal(value)

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

    def _group_corporate_actions_by_instrument(
        self,
        rows: list[LedgerCorporateActionRecord],
    ) -> dict[str, list[LedgerCorporateActionRecord]]:
        """Group deterministic quantity adjustments by instrument."""

        grouped: dict[str, list[LedgerCorporateActionRecord]] = {}
        for row in rows:
            grouped.setdefault(str(row.instrument_id), []).append(row)
        return grouped

    def _trade_adjustment_factor(
        self,
        trade: LedgerTradeFillRecord,
        actions: list[LedgerCorporateActionRecord],
    ) -> Decimal:
        """Restate pre-action trades into the current post-action quantity basis."""

        factor = Decimal("1")
        for action in actions:
            if trade.report_date_local < action.report_date_local:
                action_factor = Decimal(action.adjustment_factor)
                if action_factor <= Decimal("0"):
                    raise ValueError("corporate-action adjustment_factor must be positive")
                factor *= action_factor
        return factor

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

    def _build_open_cost_basis(self, open_lots: tuple[FifoOpenLotResult, ...]) -> str | None:
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
