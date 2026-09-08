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
        broker_position_match_count: Broker rows matching canonical FIFO quantity.
        broker_position_mismatch_count: Broker rows differing from canonical FIFO quantity.
        broker_only_position_count: Broker rows without canonical trade or cashflow history.
        broker_absent_nonzero_fifo_count: Nonzero FIFO positions absent from broker rows.
        full_rebuild_reason: Reason an incremental request was widened to a full build.
    """

    report_date_local: str
    snapshot_row_count: int
    position_lot_row_count: int
    missing_solid_valuation_count: int
    broker_position_match_count: int = 0
    broker_position_mismatch_count: int = 0
    broker_only_position_count: int = 0
    broker_absent_nonzero_fifo_count: int = 0
    full_rebuild_reason: str | None = None


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
        functional_currency: str,
        affected_conids: frozenset[str] | None = None,
        affected_currencies: frozenset[str] | None = None,
    ) -> SnapshotBuildResult:
        """Build and persist day-level snapshots for one account context.

        Args:
            account_id: Internal account identifier.
            ingestion_run_id: Optional ingestion run identifier.
            report_date_local: Flex statement business date in YYYY-MM-DD format.
            functional_currency: Explicit functional/base currency code.

        Returns:
            SnapshotBuildResult: Persistence summary for this snapshot build run.

        Raises:
            ValueError: Raised when input values are invalid.
            RuntimeError: Raised when required canonical trade data is unavailable.
        """

        normalized_account_id = account_id.strip()
        if not normalized_account_id:
            raise ValueError("account_id must not be blank")
        normalized_functional_currency = functional_currency.strip().upper()
        if not normalized_functional_currency:
            raise ValueError("functional_currency must not be blank")

        try:
            parsed_report_date = date.fromisoformat(report_date_local)
        except (TypeError, ValueError) as error:
            raise ValueError("report_date_local must use YYYY-MM-DD format") from error
        normalized_report_date = parsed_report_date.isoformat()

        is_full_build = affected_conids is None and affected_currencies is None
        full_rebuild_reason: str | None = None
        if not is_full_build and self._repository.db_pnl_snapshot_daily_count(
            account_id=normalized_account_id,
            report_date_from=normalized_report_date,
            report_date_to=normalized_report_date,
        ) == 0:
            is_full_build = True
            full_rebuild_reason = "missing_report_date_baseline"
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
        open_position_valuation_rows = (
            self._repository.db_ledger_open_position_valuation_list_for_run(
                account_id=normalized_account_id,
                ingestion_run_id=ingestion_run_id,
                instrument_ids=instrument_ids,
            )
            if ingestion_run_id is not None
            else []
        )

        fx_currencies: tuple[str, ...] | None = None
        if instrument_ids is not None:
            required_currencies = set(affected_currencies or ())
            required_currencies.add(normalized_functional_currency)
            required_currencies.update(self._repository.db_ledger_instrument_currency_list(instrument_ids))
            required_currencies.update(row.currency for row in trade_rows)
            required_currencies.update(row.commission_currency for row in trade_rows if row.commission_currency)
            required_currencies.update(row.functional_currency for row in trade_rows)
            required_currencies.update(row.currency for row in cashflow_rows)
            required_currencies.update(row.functional_currency for row in cashflow_rows)
            required_currencies.update(row.currency for row in open_position_valuation_rows)
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
        open_position_valuation_map = self._build_open_position_valuation_map(open_position_valuation_rows)

        trades_by_instrument = self._group_trades_by_instrument(trade_rows)
        cashflows_by_instrument = self._group_cashflows_by_instrument(cashflow_rows)
        corporate_actions_by_instrument = self._group_corporate_actions_by_instrument(corporate_action_rows)

        snapshot_requests: list[PnlSnapshotDailyUpsertRequest] = []
        position_lot_requests: list[PositionLotUpsertRequest] = []
        missing_solid_valuation_count = 0
        broker_position_match_count = 0
        broker_position_mismatch_count = 0
        broker_only_position_count = 0
        broker_absent_nonzero_fifo_count = 0

        instrument_keys = set(trades_by_instrument) | set(cashflows_by_instrument) | set(open_position_valuation_map)
        if instrument_ids is not None:
            instrument_keys.intersection_update(instrument_ids)
        instrument_asset_categories = self._repository.db_ledger_instrument_asset_category_map(
            account_id=normalized_account_id,
            instrument_ids=tuple(sorted(instrument_keys)),
        )
        missing_metadata = instrument_keys - set(instrument_asset_categories)
        if missing_metadata:
            raise RuntimeError(
                "missing instrument asset-category metadata for "
                f"instrument_ids={sorted(missing_metadata)}"
            )

        for instrument_id in sorted(instrument_keys):
            instrument_trades = trades_by_instrument.get(instrument_id, [])
            instrument_cashflows = cashflows_by_instrument.get(instrument_id, [])
            valuation_record = open_position_valuation_map.get(instrument_id)
            has_canonical_history = bool(instrument_trades or instrument_cashflows)
            broker_eligible = instrument_asset_categories[instrument_id].strip().upper() not in {
                "CASH",
                "FX",
            }
            converted_trades: list[FifoTradeFillInput] = []
            actions = corporate_actions_by_instrument.get(instrument_id, [])
            adjusted_position = Decimal("0")
            rounded_position = Decimal("0")
            fx_sources: set[str] = set()
            missing_fx = False
            for trade in instrument_trades:
                contract_multiplier = self._trade_contract_multiplier(trade)
                adjustment_factor = self._trade_adjustment_factor(
                    trade,
                    actions,
                )
                trade_fx_rate, trade_fx_source = self._resolve_fx_rate(
                    currency=trade.currency,
                    functional_currency=normalized_functional_currency,
                    report_date_local=trade.report_date_local,
                    fx_rate_rows=fx_rate_rows,
                    trade=trade,
                )
                fx_sources.add(trade_fx_source)
                if trade_fx_rate is None:
                    missing_fx = True
                    trade_fx_rate = Decimal("0")
                commission = abs(Decimal(trade.commission or "0"))
                commission_fx_rate = trade_fx_rate
                commission_currency = (trade.commission_currency or trade.currency).strip().upper()
                if commission and commission_currency != trade.currency.strip().upper():
                    resolved_commission_fx_rate, commission_fx_source = self._resolve_fx_rate(
                        currency=commission_currency,
                        functional_currency=normalized_functional_currency,
                        report_date_local=trade.report_date_local,
                        fx_rate_rows=fx_rate_rows,
                    )
                    fx_sources.add(commission_fx_source)
                    if resolved_commission_fx_rate is None:
                        missing_fx = True
                        commission_fx_rate = Decimal("0")
                    else:
                        commission_fx_rate = resolved_commission_fx_rate
                quantity = Decimal(trade.quantity)
                price = Decimal(trade.price) * contract_multiplier * trade_fx_rate
                if actions:
                    # Round the running position, not each fill independently:
                    # three one-share buys in a 1-for-3 split must total one share.
                    adjusted_position += abs(quantity) * adjustment_factor * (1 if trade.side == "BUY" else -1)
                    next_position = adjusted_position.quantize(Decimal("0.00000001"))
                    adjusted_quantity = abs(next_position - rounded_position)
                    if adjusted_quantity == 0:
                        raise ValueError("split creates a fill below the supported eight-decimal share precision")
                    # Preserve each fill's total cost when allocating the rounding
                    # remainder across its lots.
                    price = price * abs(quantity) / adjusted_quantity
                    quantity = adjusted_quantity
                    rounded_position = next_position
                converted_trades.append(
                    FifoTradeFillInput(
                        event_trade_fill_id=str(trade.event_trade_fill_id),
                        source_raw_record_id=str(trade.source_raw_record_id),
                        trade_timestamp_utc=trade.trade_timestamp_utc.isoformat(),
                        side=trade.side,
                        quantity=quantity,
                        price=price,
                        fees=abs(Decimal(trade.fees or "0")) * trade_fx_rate + commission * commission_fx_rate,
                        transaction_id=trade.transaction_id,
                        withholding_tax=Decimal("0"),
                    )
                )

            fifo_result = fifo_compute_instrument(
                FifoLedgerComputationRequest(
                    account_id=normalized_account_id,
                    instrument_id=instrument_id,
                    functional_currency=normalized_functional_currency,
                    mark_price=Decimal("0"),
                    trades=converted_trades,
                )
            )
            fifo_cost_basis = self._build_open_cost_basis(fifo_result.open_lots)

            cashflow_amount_total = Decimal("0")
            cashflow_fees_total = Decimal("0")
            withholding_tax_total = Decimal("0")
            cashflow_deductions_total = Decimal("0")
            for cashflow in instrument_cashflows:
                cashflow_fx_rate, cashflow_fx_source = self._resolve_fx_rate(
                    currency=cashflow.currency,
                    functional_currency=normalized_functional_currency,
                    report_date_local=cashflow.report_date_local,
                    fx_rate_rows=fx_rate_rows,
                )
                cashflow_amount_in_base = self._decimal_or_none(cashflow.amount_in_base)
                amount_in_functional_currency = (
                    cashflow_amount_in_base is not None
                    and cashflow.functional_currency.strip().upper() == normalized_functional_currency
                )
                if cashflow_fx_rate is None:
                    if not amount_in_functional_currency or Decimal(cashflow.fees or "0") != Decimal("0") or Decimal(
                        cashflow.withholding_tax or "0"
                    ) != Decimal("0"):
                        missing_fx = True
                    cashflow_fx_rate = Decimal("0")
                if amount_in_functional_currency:
                    assert cashflow_amount_in_base is not None
                    converted_amount = cashflow_amount_in_base
                    fx_sources.add("cashflow_amount_in_base")
                else:
                    converted_amount = Decimal(cashflow.amount) * cashflow_fx_rate
                cashflow_amount_total += converted_amount
                fx_sources.add(cashflow_fx_source)
                converted_fees = Decimal(cashflow.fees or "0") * cashflow_fx_rate
                converted_tax = Decimal(cashflow.withholding_tax or "0") * cashflow_fx_rate
                cashflow_deductions_total += converted_fees + converted_tax
                cashflow_fees_total += converted_fees
                withholding_tax_total += converted_tax
                # Standalone expenses already affect net cash; classify their signed amount only for reporting.
                if cashflow.cash_action == "Other Fees":
                    cashflow_fees_total -= converted_amount
                elif cashflow.cash_action == "Withholding Tax":
                    withholding_tax_total -= converted_amount

            trade_fee_total = sum((trade.fees or Decimal("0") for trade in converted_trades), Decimal("0"))
            total_fee_impact = trade_fee_total + cashflow_fees_total
            realized_pnl = fifo_result.realized_pnl + cashflow_amount_total - cashflow_deductions_total
            snapshot_position_quantity = fifo_result.position_quantity
            snapshot_cost_basis = fifo_cost_basis
            unrealized_pnl = Decimal("0")
            missing_valuation = False
            valuation_source = "no_open_position"

            if ingestion_run_id is None or not broker_eligible:
                local_mark_price, valuation_source = self._resolve_mark_price(
                    trades=instrument_trades,
                    valuation_record=None,
                    position_quantity=fifo_result.position_quantity,
                    report_date_local=parsed_report_date,
                    corporate_actions=corporate_actions_by_instrument.get(instrument_id, []),
                )
                missing_valuation = fifo_result.position_quantity != Decimal("0") and local_mark_price is None
                mark_price_base = Decimal("0")
                if local_mark_price is not None and fifo_result.position_quantity != Decimal("0"):
                    mark_fx_rate, mark_fx_source = self._resolve_fx_rate(
                        currency=instrument_trades[-1].currency,
                        functional_currency=normalized_functional_currency,
                        report_date_local=parsed_report_date,
                        fx_rate_rows=fx_rate_rows,
                    )
                    fx_sources.add(mark_fx_source)
                    if mark_fx_rate is None:
                        missing_fx = True
                    else:
                        mark_price_base = local_mark_price * mark_fx_rate
                marked_fifo_result = fifo_compute_instrument(
                    FifoLedgerComputationRequest(
                        account_id=normalized_account_id,
                        instrument_id=instrument_id,
                        functional_currency=normalized_functional_currency,
                        mark_price=mark_price_base,
                        trades=converted_trades,
                    )
                )
                unrealized_pnl = marked_fifo_result.unrealized_pnl
            else:
                broker_position_quantity = (
                    Decimal(valuation_record.position_qty) if valuation_record is not None else Decimal("0")
                )
                snapshot_position_quantity = broker_position_quantity
                quantities_match = fifo_result.position_quantity == broker_position_quantity
                if valuation_record is not None and has_canonical_history:
                    if quantities_match:
                        broker_position_match_count += 1
                    else:
                        broker_position_mismatch_count += 1
                elif valuation_record is not None:
                    broker_only_position_count += 1
                elif fifo_result.position_quantity != Decimal("0"):
                    broker_absent_nonzero_fifo_count += 1

                if valuation_record is None:
                    snapshot_cost_basis = fifo_cost_basis if quantities_match else None
                    unrealized_pnl = Decimal("0")
                    valuation_source = "broker_position_absent"
                else:
                    valuation_currency = valuation_record.currency.strip().upper()
                    valuation_fx_rate: Decimal | None
                    valuation_fx_source: str
                    if valuation_currency == normalized_functional_currency:
                        valuation_fx_rate = Decimal("1")
                        valuation_fx_source = "base_currency"
                    else:
                        valuation_fx_rate = self._positive_decimal_or_none(valuation_record.fx_rate_to_base)
                        if valuation_fx_rate is not None:
                            valuation_fx_source = "openpositions_fx_rate_to_base"
                        else:
                            valuation_fx_rate, valuation_fx_source = self._resolve_fx_rate(
                                currency=valuation_record.currency,
                                functional_currency=normalized_functional_currency,
                                report_date_local=parsed_report_date,
                                fx_rate_rows=fx_rate_rows,
                            )
                    fx_sources.add(valuation_fx_source)

                    broker_cost = self._decimal_or_none(valuation_record.cost_basis_money)
                    converted_broker_cost = (
                        broker_cost * valuation_fx_rate
                        if broker_cost is not None and valuation_fx_rate is not None
                        else None
                    )
                    snapshot_cost_basis = fifo_cost_basis if quantities_match else (
                        str(converted_broker_cost) if converted_broker_cost is not None else None
                    )

                    broker_unrealized = self._decimal_or_none(valuation_record.broker_unrealized_pnl)
                    if broker_position_quantity == Decimal("0"):
                        unrealized_pnl = Decimal("0")
                        valuation_source = "no_open_position"
                    elif quantities_match:
                        mark_price = self._decimal_or_none(valuation_record.mark_price)
                        multiplier = self._positive_decimal_or_none(valuation_record.multiplier)
                        cost_basis = self._decimal_or_none(fifo_cost_basis)
                        if (
                            mark_price is not None
                            and multiplier is not None
                            and cost_basis is not None
                            and valuation_fx_rate is not None
                        ):
                            market_value = broker_position_quantity * mark_price * multiplier * valuation_fx_rate
                            unrealized_pnl = market_value - cost_basis
                            valuation_source = "openpositions_mark_price"
                        else:
                            missing_valuation = True
                            valuation_source = "EOD_MARK_MISSING_ALL_SOURCES"
                    elif broker_unrealized is not None and valuation_fx_rate is not None:
                        unrealized_pnl = broker_unrealized * valuation_fx_rate
                        valuation_source = "openpositions_unrealized_pnl"
                    else:
                        mark_price = self._decimal_or_none(valuation_record.mark_price)
                        multiplier = self._positive_decimal_or_none(valuation_record.multiplier)
                        cost_basis = self._decimal_or_none(snapshot_cost_basis)
                        if (
                            mark_price is not None
                            and multiplier is not None
                            and cost_basis is not None
                            and valuation_fx_rate is not None
                        ):
                            market_value = broker_position_quantity * mark_price * multiplier * valuation_fx_rate
                            unrealized_pnl = market_value - cost_basis
                            valuation_source = "openpositions_mark_price"
                        else:
                            missing_valuation = True
                            valuation_source = "EOD_MARK_MISSING_ALL_SOURCES"

                    broker_money_present = any(
                        value is not None
                        for value in (
                            valuation_record.cost_basis_money,
                            valuation_record.broker_unrealized_pnl,
                            valuation_record.mark_price,
                        )
                    )
                    if valuation_fx_rate is None and broker_money_present:
                        missing_fx = True
                        if snapshot_cost_basis != fifo_cost_basis or not quantities_match:
                            snapshot_cost_basis = None
                        if broker_unrealized is not None:
                            missing_valuation = True

            total_pnl = realized_pnl + unrealized_pnl
            quantity_mismatch = ingestion_run_id is not None and snapshot_position_quantity != fifo_result.position_quantity
            broker_only = ingestion_run_id is not None and valuation_record is not None and not has_canonical_history
            provisional = (
                missing_valuation or missing_fx or quantity_mismatch or broker_only
                or valuation_source == "trades_last_trade_price"
            )
            fx_source = ",".join(sorted(fx_sources)) if fx_sources else "base_currency"

            snapshot_requests.append(
                PnlSnapshotDailyUpsertRequest(
                    account_id=normalized_account_id,
                    report_date_local=normalized_report_date,
                    instrument_id=instrument_id,
                    position_qty=str(snapshot_position_quantity),
                    cost_basis=snapshot_cost_basis,
                    realized_pnl=str(realized_pnl),
                    unrealized_pnl=str(unrealized_pnl),
                    total_pnl=str(total_pnl),
                    fees=str(total_fee_impact),
                    withholding_tax=str(withholding_tax_total),
                    currency=normalized_functional_currency,
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
            broker_position_match_count=broker_position_match_count,
            broker_position_mismatch_count=broker_position_mismatch_count,
            broker_only_position_count=broker_only_position_count,
            broker_absent_nonzero_fifo_count=broker_absent_nonzero_fifo_count,
            full_rebuild_reason=full_rebuild_reason,
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
        corporate_actions: list[LedgerCorporateActionRecord],
    ) -> tuple[Decimal | None, str]:
        """Resolve the frozen EOD mark hierarchy for one instrument."""

        if position_quantity == Decimal("0"):
            return Decimal("0"), "no_open_position"
        if valuation_record is not None and Decimal(valuation_record.position_qty) == position_quantity:
            mark_price = self._positive_decimal_or_none(valuation_record.mark_price)
            multiplier = self._positive_decimal_or_none(valuation_record.multiplier)
            if mark_price is not None and multiplier is not None:
                return mark_price * multiplier, "openpositions_mark_price"

        same_day_close_prices = [
            Decimal(trade.close_price) * self._trade_contract_multiplier(trade)
            for trade in trades
            if trade.report_date_local == report_date_local and trade.close_price is not None
        ]
        if same_day_close_prices:
            return same_day_close_prices[-1], "trades_close_price"

        eligible_trades = [trade for trade in trades if trade.report_date_local <= report_date_local]
        if eligible_trades:
            selected_trade = eligible_trades[-1]
            return (
                Decimal(selected_trade.price) * self._trade_contract_multiplier(selected_trade)
                / self._trade_adjustment_factor(selected_trade, corporate_actions),
                "trades_last_trade_price",
            )
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

    def _trade_contract_multiplier(self, trade: LedgerTradeFillRecord) -> Decimal:
        """Resolve a validated raw contract multiplier for FIFO unit economics."""

        multiplier = self._positive_decimal_or_none(trade.multiplier)
        if trade.asset_category.strip().upper() == "OPT":
            if multiplier is None:
                raise ValueError("OPT trade multiplier must be positive")
            return multiplier
        if trade.multiplier is None:
            return Decimal("1")
        if multiplier is None:
            raise ValueError("trade multiplier must be positive")
        return multiplier

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
                if lot.open_quantity != Decimal("0")
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
