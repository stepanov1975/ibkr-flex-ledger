"""Database service for Task 7 ledger inputs and snapshot persistence."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.db.interfaces import (
    LedgerCashflowRecord,
    LedgerCorporateActionRecord,
    LedgerFxRateRecord,
    LedgerOpenPositionValuationRecord,
    LedgerSnapshotRepositoryPort,
    LedgerTradeFillRecord,
    PnlSnapshotDailyRecord,
    PnlSnapshotDailyUpsertRequest,
    PositionLotUpsertRequest,
    SnapshotCleanupCandidate,
)


class SQLAlchemyLedgerSnapshotService(LedgerSnapshotRepositoryPort):
    """SQLAlchemy implementation for Task 7 ledger and snapshot DB operations."""

    _SNAPSHOT_ALLOWED_SORT_FIELDS = {
        "report_date_local": "report_date_local",
        "instrument_id": "instrument_id",
        "total_pnl": "total_pnl",
        "created_at_utc": "created_at_utc",
    }
    _SNAPSHOT_ALLOWED_SORT_DIRECTIONS = {"asc", "desc"}

    _SNAPSHOT_SELECT_COLUMNS = (
        "SELECT "
        "pnl_snapshot_daily_id, account_id, report_date_local, instrument_id, position_qty, cost_basis, "
        "realized_pnl, unrealized_pnl, total_pnl, fees, withholding_tax, currency, provisional, "
        "valuation_source, fx_source, ingestion_run_id, created_at_utc "
        "FROM pnl_snapshot_daily "
    )

    _SNAPSHOT_LIST_QUERY_BY_SORT = {
        ("report_date_local", "asc"): _SNAPSHOT_SELECT_COLUMNS
        + "WHERE account_id = :account_id AND (CAST(:report_date_from AS date) IS NULL OR report_date_local >= CAST(:report_date_from AS date)) "
        + "AND (CAST(:report_date_to AS date) IS NULL OR report_date_local <= CAST(:report_date_to AS date)) "
        + "ORDER BY report_date_local asc, instrument_id asc LIMIT :limit OFFSET :offset",
        ("report_date_local", "desc"): _SNAPSHOT_SELECT_COLUMNS
        + "WHERE account_id = :account_id AND (CAST(:report_date_from AS date) IS NULL OR report_date_local >= CAST(:report_date_from AS date)) "
        + "AND (CAST(:report_date_to AS date) IS NULL OR report_date_local <= CAST(:report_date_to AS date)) "
        + "ORDER BY report_date_local desc, instrument_id asc LIMIT :limit OFFSET :offset",
        ("instrument_id", "asc"): _SNAPSHOT_SELECT_COLUMNS
        + "WHERE account_id = :account_id AND (CAST(:report_date_from AS date) IS NULL OR report_date_local >= CAST(:report_date_from AS date)) "
        + "AND (CAST(:report_date_to AS date) IS NULL OR report_date_local <= CAST(:report_date_to AS date)) "
        + "ORDER BY instrument_id asc, report_date_local desc LIMIT :limit OFFSET :offset",
        ("instrument_id", "desc"): _SNAPSHOT_SELECT_COLUMNS
        + "WHERE account_id = :account_id AND (CAST(:report_date_from AS date) IS NULL OR report_date_local >= CAST(:report_date_from AS date)) "
        + "AND (CAST(:report_date_to AS date) IS NULL OR report_date_local <= CAST(:report_date_to AS date)) "
        + "ORDER BY instrument_id desc, report_date_local desc LIMIT :limit OFFSET :offset",
        ("total_pnl", "asc"): _SNAPSHOT_SELECT_COLUMNS
        + "WHERE account_id = :account_id AND (CAST(:report_date_from AS date) IS NULL OR report_date_local >= CAST(:report_date_from AS date)) "
        + "AND (CAST(:report_date_to AS date) IS NULL OR report_date_local <= CAST(:report_date_to AS date)) "
        + "ORDER BY total_pnl asc, report_date_local desc, instrument_id asc LIMIT :limit OFFSET :offset",
        ("total_pnl", "desc"): _SNAPSHOT_SELECT_COLUMNS
        + "WHERE account_id = :account_id AND (CAST(:report_date_from AS date) IS NULL OR report_date_local >= CAST(:report_date_from AS date)) "
        + "AND (CAST(:report_date_to AS date) IS NULL OR report_date_local <= CAST(:report_date_to AS date)) "
        + "ORDER BY total_pnl desc, report_date_local desc, instrument_id asc LIMIT :limit OFFSET :offset",
        ("created_at_utc", "asc"): _SNAPSHOT_SELECT_COLUMNS
        + "WHERE account_id = :account_id AND (CAST(:report_date_from AS date) IS NULL OR report_date_local >= CAST(:report_date_from AS date)) "
        + "AND (CAST(:report_date_to AS date) IS NULL OR report_date_local <= CAST(:report_date_to AS date)) "
        + "ORDER BY created_at_utc asc, pnl_snapshot_daily_id asc LIMIT :limit OFFSET :offset",
        ("created_at_utc", "desc"): _SNAPSHOT_SELECT_COLUMNS
        + "WHERE account_id = :account_id AND (CAST(:report_date_from AS date) IS NULL OR report_date_local >= CAST(:report_date_from AS date)) "
        + "AND (CAST(:report_date_to AS date) IS NULL OR report_date_local <= CAST(:report_date_to AS date)) "
        + "ORDER BY created_at_utc desc, pnl_snapshot_daily_id desc LIMIT :limit OFFSET :offset",
    }
    _SNAPSHOT_UNSUPPORTED_SCOPE_CTE = (
        "WITH scoped_owner_runs AS ("
        "SELECT DISTINCT artifact.ingestion_run_id "
        "FROM raw_artifact artifact "
        "WHERE artifact.account_id = :account_id "
        "AND artifact.period_key = :period_key "
        "AND artifact.flex_query_id = :flex_query_id"
        ") "
    )
    _SNAPSHOT_UNSUPPORTED_LIST_QUERY = (
        _SNAPSHOT_UNSUPPORTED_SCOPE_CTE
        + "SELECT snapshot.report_date_local, count(*) AS row_count "
        "FROM pnl_snapshot_daily snapshot "
        "WHERE snapshot.account_id = :account_id "
        "AND snapshot.ingestion_run_id IN (SELECT ingestion_run_id FROM scoped_owner_runs) "
        "AND NOT (snapshot.report_date_local = ANY(CAST(:supported_report_dates AS date[]))) "
        "GROUP BY snapshot.report_date_local "
        "ORDER BY snapshot.report_date_local"
    )
    _SNAPSHOT_UNSUPPORTED_DELETE_QUERY = (
        _SNAPSHOT_UNSUPPORTED_SCOPE_CTE
        + "DELETE FROM pnl_snapshot_daily snapshot "
        "WHERE snapshot.account_id = :account_id "
        "AND snapshot.ingestion_run_id IN (SELECT ingestion_run_id FROM scoped_owner_runs) "
        "AND NOT (snapshot.report_date_local = ANY(CAST(:supported_report_dates AS date[])))"
    )

    def __init__(self, engine: Engine):
        """Initialize ledger/snapshot database service.

        Args:
            engine: SQLAlchemy engine used for persistence and reads.

        Returns:
            None: Initializer does not return values.

        Raises:
            ValueError: Raised when engine is invalid.
        """

        if engine is None:
            raise ValueError("engine must not be None")
        self._engine = engine

    def db_ledger_instrument_ids_for_scope(
        self,
        account_id: str,
        conids: tuple[str, ...],
        currencies: tuple[str, ...],
    ) -> list[str]:
        """Resolve instruments matching affected conids or source currencies."""

        normalized_account_id = self._db_ledger_validate_non_empty_text(account_id, "account_id")
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT instrument_id FROM instrument "
                        "WHERE account_id = :account_id "
                        "AND (conid = ANY(:conids) OR currency = ANY(:currencies)) "
                        "ORDER BY instrument_id"
                    ),
                    {
                        "account_id": normalized_account_id,
                        "conids": list(conids),
                        "currencies": list(currencies),
                    },
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("ledger instrument scope read failed") from error
        return [str(row["instrument_id"]) for row in rows]

    def db_ledger_instrument_currency_list(
        self,
        instrument_ids: tuple[str, ...],
    ) -> list[str]:
        """List distinct currencies for selected canonical instruments."""

        normalized_instrument_ids = tuple(
            self._db_ledger_validate_uuid_text(instrument_id, "instrument_ids")
            for instrument_id in instrument_ids
        )
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT DISTINCT currency FROM instrument "
                        "WHERE instrument_id = ANY(CAST(:instrument_ids AS uuid[])) "
                        "ORDER BY currency"
                    ),
                    {"instrument_ids": list(normalized_instrument_ids)},
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("ledger instrument currency read failed") from error
        return [row["currency"] for row in rows]

    def db_ledger_instrument_asset_category_map(
        self,
        account_id: str,
        instrument_ids: tuple[str, ...],
    ) -> dict[str, str]:
        """Map selected account instruments to canonical asset categories."""

        normalized_account_id = self._db_ledger_validate_non_empty_text(account_id, "account_id")
        normalized_instrument_ids = tuple(
            self._db_ledger_validate_uuid_text(instrument_id, "instrument_ids")
            for instrument_id in instrument_ids
        )
        if not normalized_instrument_ids:
            return {}
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT instrument_id, asset_category FROM instrument "
                        "WHERE account_id = :account_id "
                        "AND instrument_id = ANY(CAST(:instrument_ids AS uuid[])) "
                        "ORDER BY instrument_id"
                    ),
                    {
                        "account_id": normalized_account_id,
                        "instrument_ids": list(normalized_instrument_ids),
                    },
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("ledger instrument asset-category read failed") from error
        return {
            str(row["instrument_id"]): row["asset_category"]
            for row in rows
        }

    def db_ledger_trade_fill_list_for_account(
        self,
        account_id: str,
        through_report_date_local: str | None = None,
        instrument_ids: tuple[str, ...] | None = None,
    ) -> list[LedgerTradeFillRecord]:
        """List trade-fill rows for FIFO computation in deterministic order.

        Args:
            account_id: Internal account identifier.
            through_report_date_local: Optional inclusive local-date upper bound.

        Returns:
            list[LedgerTradeFillRecord]: Deterministically ordered trade fills.

        Raises:
            ValueError: Raised when input values are invalid.
            RuntimeError: Raised when database read fails.
        """

        normalized_account_id = self._db_ledger_validate_non_empty_text(account_id, "account_id")
        normalized_through_date = self._db_ledger_validate_optional_date_text(
            through_report_date_local,
            "through_report_date_local",
        )

        statement = (
            "SELECT "
            "etf.event_trade_fill_id, etf.account_id, etf.instrument_id, etf.source_raw_record_id, "
            "etf.trade_timestamp_utc, etf.report_date_local, etf.side, etf.quantity, etf.price, "
            "etf.fees, etf.commission, etf.functional_currency, etf.currency, etf.transaction_id, "
            "etf.net_cash, etf.net_cash_in_base, etf.fx_rate_to_base, "
            "i.asset_category, "
            "CASE WHEN BTRIM(COALESCE(rr.source_payload->>'multiplier', '')) IN ('', '-', '--', 'N/A') "
            "THEN NULL ELSE REPLACE(BTRIM(rr.source_payload->>'multiplier'), ',', '')::numeric END AS multiplier, "
            "CASE WHEN BTRIM(COALESCE(rr.source_payload->>'closePrice', '')) IN ('', '-', '--', 'N/A') "
            "THEN NULL ELSE REPLACE(BTRIM(rr.source_payload->>'closePrice'), ',', '')::numeric END AS close_price "
            "FROM event_trade_fill etf "
            "JOIN instrument i ON i.instrument_id = etf.instrument_id AND i.account_id = etf.account_id "
            "LEFT JOIN raw_record rr ON rr.raw_record_id = etf.source_raw_record_id "
            "WHERE etf.account_id = :account_id "
            "AND (CAST(:through_report_date_local AS date) IS NULL "
            "OR etf.report_date_local <= CAST(:through_report_date_local AS date)) "
        )
        parameters: dict[str, Any] = {
            "account_id": normalized_account_id,
            "through_report_date_local": normalized_through_date,
        }
        if instrument_ids is not None:
            parameters["instrument_ids"] = [
                self._db_ledger_validate_uuid_text(instrument_id, "instrument_ids")
                for instrument_id in instrument_ids
            ]
            statement += "AND etf.instrument_id = ANY(CAST(:instrument_ids AS uuid[])) "
        statement += (
            "ORDER BY etf.trade_timestamp_utc asc, "
            "CASE WHEN etf.transaction_id ~ '^[0-9]+$' THEN CAST(etf.transaction_id AS numeric) END asc NULLS FIRST, "
            "etf.transaction_id asc NULLS FIRST, "
            "etf.source_raw_record_id asc, etf.event_trade_fill_id asc"
        )

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(statement),
                    parameters,
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("ledger trade-fill read failed") from error

        return [
            LedgerTradeFillRecord(
                event_trade_fill_id=row["event_trade_fill_id"],
                account_id=row["account_id"],
                instrument_id=row["instrument_id"],
                source_raw_record_id=row["source_raw_record_id"],
                trade_timestamp_utc=row["trade_timestamp_utc"],
                report_date_local=row["report_date_local"],
                side=row["side"],
                quantity=str(row["quantity"]),
                price=str(row["price"]),
                fees=None if row["fees"] is None else str(row["fees"]),
                commission=None if row["commission"] is None else str(row["commission"]),
                functional_currency=row["functional_currency"],
                currency=row["currency"],
                transaction_id=row["transaction_id"],
                net_cash=None if row["net_cash"] is None else str(row["net_cash"]),
                net_cash_in_base=None if row["net_cash_in_base"] is None else str(row["net_cash_in_base"]),
                fx_rate_to_base=None if row["fx_rate_to_base"] is None else str(row["fx_rate_to_base"]),
                close_price=None if row["close_price"] is None else str(row["close_price"]),
                asset_category=row["asset_category"],
                multiplier=None if row["multiplier"] is None else str(row["multiplier"]),
            )
            for row in rows
        ]

    def db_ledger_cashflow_list_for_account(
        self,
        account_id: str,
        through_report_date_local: str | None = None,
        instrument_ids: tuple[str, ...] | None = None,
    ) -> list[LedgerCashflowRecord]:
        """List cashflow rows for fee/withholding adjustments in deterministic order.

        Args:
            account_id: Internal account identifier.
            through_report_date_local: Optional inclusive local-date upper bound.

        Returns:
            list[LedgerCashflowRecord]: Deterministically ordered cashflows.

        Raises:
            ValueError: Raised when input values are invalid.
            RuntimeError: Raised when database read fails.
        """

        normalized_account_id = self._db_ledger_validate_non_empty_text(account_id, "account_id")
        normalized_through_date = self._db_ledger_validate_optional_date_text(
            through_report_date_local,
            "through_report_date_local",
        )

        statement = (
            "SELECT "
            "event_cashflow_id, account_id, instrument_id, report_date_local, withholding_tax, fees, "
            "functional_currency, amount, amount_in_base, currency "
            "FROM event_cashflow "
            "WHERE account_id = :account_id "
            "AND (CAST(:through_report_date_local AS date) IS NULL "
            "OR report_date_local <= CAST(:through_report_date_local AS date)) "
        )
        parameters: dict[str, Any] = {
            "account_id": normalized_account_id,
            "through_report_date_local": normalized_through_date,
        }
        if instrument_ids is not None:
            parameters["instrument_ids"] = [
                self._db_ledger_validate_uuid_text(instrument_id, "instrument_ids")
                for instrument_id in instrument_ids
            ]
            statement += "AND instrument_id = ANY(CAST(:instrument_ids AS uuid[])) "
        statement += "ORDER BY report_date_local asc, event_cashflow_id asc"

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(statement),
                    parameters,
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("ledger cashflow read failed") from error

        return [
            LedgerCashflowRecord(
                event_cashflow_id=row["event_cashflow_id"],
                account_id=row["account_id"],
                instrument_id=row["instrument_id"],
                report_date_local=row["report_date_local"],
                withholding_tax=None if row["withholding_tax"] is None else str(row["withholding_tax"]),
                fees=None if row["fees"] is None else str(row["fees"]),
                functional_currency=row["functional_currency"],
                amount=str(row["amount"]),
                amount_in_base=None if row["amount_in_base"] is None else str(row["amount_in_base"]),
                currency=row["currency"],
            )
            for row in rows
        ]

    def db_ledger_open_position_valuation_list_for_run(
        self,
        account_id: str,
        ingestion_run_id: str,
        instrument_ids: tuple[str, ...] | None = None,
    ) -> list[LedgerOpenPositionValuationRecord]:
        """List broker OpenPositions valuation rows for one ingestion run.

        Args:
            account_id: Internal account identifier.
            ingestion_run_id: Ingestion run identifier.

        Returns:
            list[LedgerOpenPositionValuationRecord]: Broker valuation rows keyed by instrument.

        Raises:
            ValueError: Raised when input values are invalid.
            RuntimeError: Raised when database read fails.
        """

        normalized_account_id = self._db_ledger_validate_non_empty_text(account_id, "account_id")
        normalized_ingestion_run_id = self._db_ledger_validate_uuid_text(ingestion_run_id, "ingestion_run_id")

        statement = (
            "WITH parsed AS ("
            "SELECT i.instrument_id, rr.raw_record_id, "
            "UPPER(BTRIM(rr.source_payload->>'assetCategory')) AS asset_category, "
            "UPPER(BTRIM(rr.source_payload->>'currency')) AS currency, "
            "REPLACE(BTRIM(COALESCE(rr.source_payload->>'position', '')), ',', '')::numeric AS position_qty, "
            "CASE WHEN BTRIM(COALESCE(rr.source_payload->>'markPrice', '')) IN ('', '-', '--', 'N/A') "
            "THEN NULL ELSE REPLACE(BTRIM(rr.source_payload->>'markPrice'), ',', '')::numeric END AS mark_price, "
            "CASE WHEN BTRIM(COALESCE(rr.source_payload->>'costBasisMoney', '')) IN ('', '-', '--', 'N/A') "
            "THEN NULL ELSE REPLACE(BTRIM(rr.source_payload->>'costBasisMoney'), ',', '')::numeric END AS cost_basis_money, "
            "CASE WHEN BTRIM(COALESCE(rr.source_payload->>'fifoPnlUnrealized', '')) IN ('', '-', '--', 'N/A') "
            "THEN NULL ELSE REPLACE(BTRIM(rr.source_payload->>'fifoPnlUnrealized'), ',', '')::numeric END "
            "AS broker_unrealized_pnl, "
            "CASE WHEN BTRIM(COALESCE(rr.source_payload->>'fxRateToBase', '')) IN ('', '-', '--', 'N/A') "
            "THEN NULL ELSE REPLACE(BTRIM(rr.source_payload->>'fxRateToBase'), ',', '')::numeric END AS fx_rate_to_base, "
            "CASE WHEN BTRIM(COALESCE(rr.source_payload->>'multiplier', '')) IN ('', '-', '--', 'N/A') "
            "THEN NULL ELSE REPLACE(BTRIM(rr.source_payload->>'multiplier'), ',', '')::numeric END AS multiplier, "
            "CASE "
            "WHEN LENGTH(COALESCE(rr.source_payload->>'reportDate', '')) = 8 "
            "THEN TO_DATE(rr.source_payload->>'reportDate', 'YYYYMMDD') "
            "ELSE CAST(NULLIF(rr.source_payload->>'reportDate', '') AS date) "
            "END AS report_date_local "
            "FROM raw_record rr "
            "JOIN instrument i ON i.account_id = rr.account_id AND i.conid = rr.source_payload->>'conid' "
            "WHERE rr.account_id = :account_id "
            "AND rr.ingestion_run_id = CAST(:ingestion_run_id AS uuid) "
            "AND rr.section_name = 'OpenPositions' "
            "AND rr.source_row_ref LIKE 'OpenPositions:OpenPosition:%' "
            "AND UPPER(BTRIM(rr.source_payload->>'assetCategory')) NOT IN ('CASH', 'FX')"
        )
        parameters: dict[str, Any] = {
            "account_id": normalized_account_id,
            "ingestion_run_id": normalized_ingestion_run_id,
        }
        if instrument_ids is not None:
            parameters["instrument_ids"] = [
                self._db_ledger_validate_uuid_text(instrument_id, "instrument_ids")
                for instrument_id in instrument_ids
            ]
            statement += " AND i.instrument_id = ANY(CAST(:instrument_ids AS uuid[]))"
        statement += (
            "), ranked AS ("
            "SELECT instrument_id, asset_category, currency, position_qty, mark_price, cost_basis_money, "
            "broker_unrealized_pnl, fx_rate_to_base, multiplier, report_date_local, "
            "ROW_NUMBER() OVER (PARTITION BY instrument_id ORDER BY raw_record_id DESC) AS row_rank "
            "FROM parsed"
            ") "
            "SELECT instrument_id, asset_category, currency, position_qty, mark_price, cost_basis_money, "
            "broker_unrealized_pnl, fx_rate_to_base, multiplier, report_date_local "
            "FROM ranked WHERE row_rank = 1"
        )

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(statement),
                    parameters,
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("ledger OpenPositions valuation read failed") from error

        return [
            LedgerOpenPositionValuationRecord(
                instrument_id=row["instrument_id"],
                asset_category=row["asset_category"],
                currency=row["currency"],
                position_qty=str(row["position_qty"]),
                mark_price=None if row["mark_price"] is None else str(row["mark_price"]),
                cost_basis_money=None
                if row["cost_basis_money"] is None
                else str(row["cost_basis_money"]),
                broker_unrealized_pnl=None
                if row["broker_unrealized_pnl"] is None
                else str(row["broker_unrealized_pnl"]),
                fx_rate_to_base=None
                if row["fx_rate_to_base"] is None
                else str(row["fx_rate_to_base"]),
                multiplier=None if row["multiplier"] is None else str(row["multiplier"]),
                report_date_local=row["report_date_local"],
            )
            for row in rows
        ]

    def db_ledger_fx_rate_list_for_account(
        self,
        account_id: str,
        through_report_date_local: str,
        currencies: tuple[str, ...] | None = None,
    ) -> list[LedgerFxRateRecord]:
        """List usable canonical conversion rates through one report date."""

        normalized_account_id = self._db_ledger_validate_non_empty_text(account_id, "account_id")
        normalized_through_date = self._db_ledger_validate_optional_date_text(
            through_report_date_local,
            "through_report_date_local",
        )
        if normalized_through_date is None:
            raise ValueError("through_report_date_local must not be blank")

        statement = (
            "SELECT report_date_local, currency, functional_currency, fx_rate, fx_source, "
            "ingestion_run_id, source_raw_record_id "
            "FROM event_fx WHERE account_id = :account_id "
            "AND report_date_local <= CAST(:through_report_date_local AS date) "
        )
        parameters: dict[str, Any] = {
            "account_id": normalized_account_id,
            "through_report_date_local": normalized_through_date,
        }
        if currencies is not None:
            parameters["currencies"] = list(currencies)
            statement += "AND event_fx.currency = ANY(:currencies) "
        statement += "ORDER BY report_date_local asc, ingestion_run_id asc, source_raw_record_id asc"

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(statement),
                    parameters,
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("ledger FX-rate read failed") from error

        return [
            LedgerFxRateRecord(
                report_date_local=row["report_date_local"],
                currency=row["currency"],
                functional_currency=row["functional_currency"],
                fx_rate=None if row["fx_rate"] is None else str(row["fx_rate"]),
                fx_source=row["fx_source"],
                ingestion_run_id=row["ingestion_run_id"],
                source_raw_record_id=row["source_raw_record_id"],
            )
            for row in rows
        ]

    def db_ledger_corporate_action_list_for_account(
        self,
        account_id: str,
        through_report_date_local: str,
        instrument_ids: tuple[str, ...] | None = None,
    ) -> list[LedgerCorporateActionRecord]:
        """List auto-classified split and stock-dividend factors."""

        normalized_account_id = self._db_ledger_validate_non_empty_text(account_id, "account_id")
        normalized_through_date = self._db_ledger_validate_optional_date_text(
            through_report_date_local, "through_report_date_local"
        )
        if normalized_through_date is None:
            raise ValueError("through_report_date_local must not be blank")
        statement = (
            "SELECT event.instrument_id, event.report_date_local, event.reorg_code AS action_type, "
            "COALESCE(NULLIF(raw.source_payload->>'ratio','')::numeric, "
            "NULLIF(raw.source_payload->>'newQuantity','')::numeric / "
            "NULLIF(NULLIF(raw.source_payload->>'oldQuantity','')::numeric, 0)) AS adjustment_factor "
            "FROM event_corp_action event JOIN raw_record raw ON raw.raw_record_id=event.source_raw_record_id "
            "WHERE event.account_id=:account_id AND event.report_date_local<=CAST(:through_date AS date) "
            "AND event.requires_manual=false AND event.reorg_code IN ('FORWARDSPLIT','REVERSESPLIT','STOCKDIV') "
            "AND event.instrument_id IS NOT NULL "
        )
        parameters: dict[str, Any] = {
            "account_id": normalized_account_id,
            "through_date": normalized_through_date,
        }
        if instrument_ids is not None:
            parameters["instrument_ids"] = [
                self._db_ledger_validate_uuid_text(instrument_id, "instrument_ids")
                for instrument_id in instrument_ids
            ]
            statement += "AND event.instrument_id = ANY(CAST(:instrument_ids AS uuid[])) "
        statement += "ORDER BY event.report_date_local asc, event.event_corp_action_id asc"
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(statement),
                    parameters,
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("ledger corporate-action read failed") from error
        return [
            LedgerCorporateActionRecord(
                instrument_id=row["instrument_id"],
                report_date_local=row["report_date_local"],
                action_type=row["action_type"],
                adjustment_factor=str(row["adjustment_factor"]),
            )
            for row in rows
            if row["adjustment_factor"] is not None
        ]

    def db_position_lot_upsert_many(self, requests: list[PositionLotUpsertRequest]) -> None:
        """UPSERT deterministic position-lot rows in one batch operation.

        Args:
            requests: Position-lot upsert requests.

        Returns:
            None: Persistence is applied as side effect.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence fails.
        """

        if requests is None:
            raise ValueError("requests must not be None")
        if len(requests) == 0:
            return

        normalized_requests = [self._db_ledger_validate_position_lot_upsert_request(request) for request in requests]

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO position_lot ("
                        "position_lot_id, account_id, instrument_id, open_event_trade_fill_id, opened_at_utc, closed_at_utc, "
                        "open_quantity, remaining_quantity, open_price, cost_basis_open, realized_pnl_to_date, status"
                        ") VALUES ("
                        "CAST(:position_lot_id AS uuid), :account_id, CAST(:instrument_id AS uuid), "
                        "CAST(:open_event_trade_fill_id AS uuid), CAST(:opened_at_utc AS timestamptz), "
                        "CAST(:closed_at_utc AS timestamptz), CAST(:open_quantity AS numeric), "
                        "CAST(:remaining_quantity AS numeric), CAST(:open_price AS numeric), CAST(:cost_basis_open AS numeric), "
                        "CAST(:realized_pnl_to_date AS numeric), :status"
                        ") ON CONFLICT (position_lot_id) DO UPDATE SET "
                        "remaining_quantity = EXCLUDED.remaining_quantity, "
                        "closed_at_utc = EXCLUDED.closed_at_utc, "
                        "realized_pnl_to_date = EXCLUDED.realized_pnl_to_date, "
                        "status = EXCLUDED.status, "
                        "updated_at_utc = now()"
                    ),
                    normalized_requests,
                )
        except SQLAlchemyError as error:
            raise RuntimeError("position lot upsert failed") from error

    def db_position_lot_reconcile_open(
        self,
        account_id: str,
        closed_at_utc: datetime,
        requests: list[PositionLotUpsertRequest],
        instrument_ids: tuple[str, ...] | None = None,
    ) -> None:
        """Close stale open lots and upsert the recomputed open-lot projection."""

        normalized_account_id = self._db_ledger_validate_non_empty_text(account_id, "account_id")
        if closed_at_utc.tzinfo is None or closed_at_utc.utcoffset() is None:
            raise ValueError("closed_at_utc must be offset-aware")
        if requests is None:
            raise ValueError("requests must not be None")
        normalized_instrument_ids = (
            None
            if instrument_ids is None
            else tuple(
                self._db_ledger_validate_uuid_text(instrument_id, "instrument_ids")
                for instrument_id in instrument_ids
            )
        )
        selected_instrument_ids = None if normalized_instrument_ids is None else set(normalized_instrument_ids)
        scoped_requests = (
            requests
            if selected_instrument_ids is None
            else [request for request in requests if str(request.instrument_id) in selected_instrument_ids]
        )
        normalized_requests = [
            self._db_ledger_validate_position_lot_upsert_request(request)
            for request in scoped_requests
        ]

        close_statement = (
            "UPDATE position_lot SET remaining_quantity = 0, status = 'closed', "
            "closed_at_utc = :closed_at_utc, updated_at_utc = now() "
            "WHERE account_id = :account_id AND status = 'open'"
        )
        close_parameters: dict[str, Any] = {
            "account_id": normalized_account_id,
            "closed_at_utc": closed_at_utc,
        }
        if normalized_instrument_ids is not None:
            close_statement += " AND instrument_id = ANY(CAST(:instrument_ids AS uuid[]))"
            close_parameters["instrument_ids"] = list(normalized_instrument_ids)

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    text(close_statement),
                    close_parameters,
                )
                if normalized_requests:
                    connection.execute(
                        text(
                            "INSERT INTO position_lot ("
                            "position_lot_id, account_id, instrument_id, open_event_trade_fill_id, opened_at_utc, "
                            "closed_at_utc, open_quantity, remaining_quantity, open_price, cost_basis_open, "
                            "realized_pnl_to_date, status) VALUES ("
                            "CAST(:position_lot_id AS uuid), :account_id, CAST(:instrument_id AS uuid), "
                            "CAST(:open_event_trade_fill_id AS uuid), CAST(:opened_at_utc AS timestamptz), "
                            "CAST(:closed_at_utc AS timestamptz), CAST(:open_quantity AS numeric), "
                            "CAST(:remaining_quantity AS numeric), CAST(:open_price AS numeric), "
                            "CAST(:cost_basis_open AS numeric), CAST(:realized_pnl_to_date AS numeric), :status) "
                            "ON CONFLICT (position_lot_id) DO UPDATE SET "
                            "open_quantity = EXCLUDED.open_quantity, "
                            "remaining_quantity = EXCLUDED.remaining_quantity, "
                            "open_price = EXCLUDED.open_price, "
                            "cost_basis_open = EXCLUDED.cost_basis_open, "
                            "closed_at_utc = EXCLUDED.closed_at_utc, "
                            "realized_pnl_to_date = EXCLUDED.realized_pnl_to_date, status = EXCLUDED.status, "
                            "updated_at_utc = now()"
                        ),
                        normalized_requests,
                    )
        except SQLAlchemyError as error:
            raise RuntimeError("position lot reconciliation failed") from error

    def db_pnl_snapshot_daily_upsert_many(self, requests: list[PnlSnapshotDailyUpsertRequest]) -> None:
        """UPSERT daily snapshot rows in one batch operation.

        Args:
            requests: Daily snapshot upsert requests.

        Returns:
            None: Persistence is applied as side effect.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence fails.
        """

        if requests is None:
            raise ValueError("requests must not be None")
        if len(requests) == 0:
            return

        normalized_requests = [self._db_ledger_validate_snapshot_upsert_request(request) for request in requests]

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO pnl_snapshot_daily ("
                        "account_id, report_date_local, instrument_id, position_qty, cost_basis, realized_pnl, unrealized_pnl, "
                        "total_pnl, fees, withholding_tax, currency, calculation_provisional, provisional, "
                        "valuation_source, fx_source, ingestion_run_id"
                        ") VALUES ("
                        ":account_id, CAST(:report_date_local AS date), CAST(:instrument_id AS uuid), "
                        "CAST(:position_qty AS numeric), CAST(:cost_basis AS numeric), CAST(:realized_pnl AS numeric), "
                        "CAST(:unrealized_pnl AS numeric), CAST(:total_pnl AS numeric), CAST(:fees AS numeric), "
                        "CAST(:withholding_tax AS numeric), :currency, :provisional, "
                        "(:provisional OR EXISTS (SELECT 1 FROM corporate_action_manual_case manual_case "
                        "WHERE manual_case.instrument_id = CAST(:instrument_id AS uuid) AND manual_case.status = 'open')), "
                        ":valuation_source, :fx_source, "
                        "CAST(:ingestion_run_id AS uuid)"
                        ") ON CONFLICT ON CONSTRAINT uq_pnl_snapshot_daily_account_date_instrument DO UPDATE SET "
                        "position_qty = EXCLUDED.position_qty, "
                        "cost_basis = EXCLUDED.cost_basis, "
                        "realized_pnl = EXCLUDED.realized_pnl, "
                        "unrealized_pnl = EXCLUDED.unrealized_pnl, "
                        "total_pnl = EXCLUDED.total_pnl, "
                        "fees = EXCLUDED.fees, "
                        "withholding_tax = EXCLUDED.withholding_tax, "
                        "currency = EXCLUDED.currency, "
                        "calculation_provisional = EXCLUDED.calculation_provisional, "
                        "provisional = EXCLUDED.provisional, "
                        "valuation_source = EXCLUDED.valuation_source, "
                        "fx_source = EXCLUDED.fx_source, "
                        "ingestion_run_id = EXCLUDED.ingestion_run_id"
                    ),
                    normalized_requests,
                )
        except SQLAlchemyError as error:
            raise RuntimeError("daily snapshot upsert failed") from error

    def db_pnl_snapshot_daily_unsupported_list(
        self,
        account_id: str,
        period_key: str,
        flex_query_id: str,
        supported_report_dates: tuple[str, ...],
    ) -> list[SnapshotCleanupCandidate]:
        """List unsupported snapshot dates within one immutable replay scope."""

        parameters = self._db_ledger_unsupported_snapshot_parameters(
            account_id,
            period_key,
            flex_query_id,
            supported_report_dates,
        )
        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(self._SNAPSHOT_UNSUPPORTED_LIST_QUERY),
                    parameters,
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("unsupported daily snapshot list failed") from error

        return [
            SnapshotCleanupCandidate(
                report_date_local=row["report_date_local"],
                row_count=int(row["row_count"]),
            )
            for row in rows
        ]

    def db_pnl_snapshot_daily_unsupported_delete(
        self,
        account_id: str,
        period_key: str,
        flex_query_id: str,
        supported_report_dates: tuple[str, ...],
    ) -> int:
        """Delete unsupported snapshots within one immutable replay scope."""

        parameters = self._db_ledger_unsupported_snapshot_parameters(
            account_id,
            period_key,
            flex_query_id,
            supported_report_dates,
        )
        try:
            with self._engine.begin() as connection:
                result = connection.execute(text(self._SNAPSHOT_UNSUPPORTED_DELETE_QUERY), parameters)
        except SQLAlchemyError as error:
            raise RuntimeError("unsupported daily snapshot delete failed") from error
        return int(result.rowcount)

    def db_pnl_snapshot_daily_list(
        self,
        account_id: str,
        limit: int,
        offset: int,
        sort_by: str,
        sort_dir: str,
        report_date_from: str | None = None,
        report_date_to: str | None = None,
    ) -> list[PnlSnapshotDailyRecord]:
        """List persisted daily snapshots for API/report surfaces.

        Args:
            account_id: Internal account identifier.
            limit: Maximum row count.
            offset: Number of rows to skip.
            sort_by: Sort field name.
            sort_dir: Sort direction (`asc` or `desc`).
            report_date_from: Optional inclusive lower report-date bound.
            report_date_to: Optional inclusive upper report-date bound.

        Returns:
            list[PnlSnapshotDailyRecord]: Deterministically ordered daily snapshots.

        Raises:
            ValueError: Raised when input values are invalid.
            RuntimeError: Raised when database read fails.
        """

        normalized_account_id = self._db_ledger_validate_non_empty_text(account_id, "account_id")
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if offset < 0:
            raise ValueError("offset must be >= 0")

        normalized_sort_by = self._db_ledger_validate_non_empty_text(sort_by, "sort_by")
        normalized_sort_dir = self._db_ledger_validate_non_empty_text(sort_dir, "sort_dir").lower()
        if normalized_sort_by not in self._SNAPSHOT_ALLOWED_SORT_FIELDS:
            raise ValueError(f"unsupported sort_by={normalized_sort_by}")
        if normalized_sort_dir not in self._SNAPSHOT_ALLOWED_SORT_DIRECTIONS:
            raise ValueError(f"unsupported sort_dir={normalized_sort_dir}")

        normalized_report_date_from = self._db_ledger_validate_optional_date_text(report_date_from, "report_date_from")
        normalized_report_date_to = self._db_ledger_validate_optional_date_text(report_date_to, "report_date_to")

        query_template = self._SNAPSHOT_LIST_QUERY_BY_SORT[(normalized_sort_by, normalized_sort_dir)]

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(query_template),
                    {
                        "account_id": normalized_account_id,
                        "limit": limit,
                        "offset": offset,
                        "report_date_from": normalized_report_date_from,
                        "report_date_to": normalized_report_date_to,
                    },
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("daily snapshot list failed") from error

        return [self._db_ledger_map_snapshot_row(row) for row in rows]

    def db_pnl_snapshot_daily_count(
        self,
        account_id: str,
        report_date_from: str | None = None,
        report_date_to: str | None = None,
    ) -> int:
        """Count account snapshots within optional date bounds."""

        normalized_account_id = self._db_ledger_validate_non_empty_text(account_id, "account_id")
        normalized_from = self._db_ledger_validate_optional_date_text(report_date_from, "report_date_from")
        normalized_to = self._db_ledger_validate_optional_date_text(report_date_to, "report_date_to")
        try:
            with self._engine.connect() as connection:
                value = connection.execute(
                    text(
                        "SELECT count(*) FROM pnl_snapshot_daily WHERE account_id=:account_id "
                        "AND (CAST(:date_from AS date) IS NULL OR report_date_local>=CAST(:date_from AS date)) "
                        "AND (CAST(:date_to AS date) IS NULL OR report_date_local<=CAST(:date_to AS date))"
                    ),
                    {"account_id": normalized_account_id, "date_from": normalized_from, "date_to": normalized_to},
                ).scalar_one()
        except SQLAlchemyError as error:
            raise RuntimeError("daily snapshot count failed") from error
        return int(value)

    def _db_ledger_unsupported_snapshot_parameters(
        self,
        account_id: str,
        period_key: str,
        flex_query_id: str,
        supported_report_dates: tuple[str, ...],
    ) -> dict[str, Any]:
        """Validate and prepare the shared unsupported-snapshot scope parameters."""

        if not supported_report_dates:
            raise ValueError("supported_report_dates must not be empty")

        return {
            "account_id": self._db_ledger_validate_non_empty_text(account_id, "account_id"),
            "period_key": self._db_ledger_validate_non_empty_text(period_key, "period_key"),
            "flex_query_id": self._db_ledger_validate_non_empty_text(flex_query_id, "flex_query_id"),
            "supported_report_dates": [
                self._db_ledger_validate_date_text(value, "supported_report_dates")
                for value in supported_report_dates
            ],
        }

    def _db_ledger_validate_position_lot_upsert_request(self, request: PositionLotUpsertRequest) -> dict[str, Any]:
        """Validate one position-lot upsert request.

        Args:
            request: Position-lot upsert request.

        Returns:
            dict[str, Any]: SQL-ready request payload.

        Raises:
            ValueError: Raised when request values are invalid.
        """

        if request is None:
            raise ValueError("request must not be None")

        status = self._db_ledger_validate_non_empty_text(request.status, "request.status")
        if status not in {"open", "closed"}:
            raise ValueError("request.status must be one of: open, closed")

        return {
            "position_lot_id": self._db_ledger_validate_uuid_text(request.position_lot_id, "request.position_lot_id"),
            "account_id": self._db_ledger_validate_non_empty_text(request.account_id, "request.account_id"),
            "instrument_id": self._db_ledger_validate_uuid_text(request.instrument_id, "request.instrument_id"),
            "open_event_trade_fill_id": self._db_ledger_validate_uuid_text(
                request.open_event_trade_fill_id,
                "request.open_event_trade_fill_id",
            ),
            "opened_at_utc": request.opened_at_utc.isoformat(),
            "closed_at_utc": None if request.closed_at_utc is None else request.closed_at_utc.isoformat(),
            "open_quantity": self._db_ledger_validate_non_empty_text(request.open_quantity, "request.open_quantity"),
            "remaining_quantity": self._db_ledger_validate_non_empty_text(
                request.remaining_quantity,
                "request.remaining_quantity",
            ),
            "open_price": self._db_ledger_validate_non_empty_text(request.open_price, "request.open_price"),
            "cost_basis_open": self._db_ledger_validate_non_empty_text(
                request.cost_basis_open,
                "request.cost_basis_open",
            ),
            "realized_pnl_to_date": self._db_ledger_validate_non_empty_text(
                request.realized_pnl_to_date,
                "request.realized_pnl_to_date",
            ),
            "status": status,
        }

    def _db_ledger_validate_snapshot_upsert_request(self, request: PnlSnapshotDailyUpsertRequest) -> dict[str, Any]:
        """Validate one daily snapshot upsert request.

        Args:
            request: Daily snapshot upsert request.

        Returns:
            dict[str, Any]: SQL-ready request payload.

        Raises:
            ValueError: Raised when request values are invalid.
        """

        if request is None:
            raise ValueError("request must not be None")

        return {
            "account_id": self._db_ledger_validate_non_empty_text(request.account_id, "request.account_id"),
            "report_date_local": self._db_ledger_validate_date_text(request.report_date_local, "request.report_date_local"),
            "instrument_id": self._db_ledger_validate_uuid_text(request.instrument_id, "request.instrument_id"),
            "position_qty": self._db_ledger_validate_non_empty_text(request.position_qty, "request.position_qty"),
            "cost_basis": request.cost_basis,
            "realized_pnl": self._db_ledger_validate_non_empty_text(request.realized_pnl, "request.realized_pnl"),
            "unrealized_pnl": self._db_ledger_validate_non_empty_text(request.unrealized_pnl, "request.unrealized_pnl"),
            "total_pnl": self._db_ledger_validate_non_empty_text(request.total_pnl, "request.total_pnl"),
            "fees": self._db_ledger_validate_non_empty_text(request.fees, "request.fees"),
            "withholding_tax": self._db_ledger_validate_non_empty_text(request.withholding_tax, "request.withholding_tax"),
            "currency": self._db_ledger_validate_non_empty_text(request.currency, "request.currency"),
            "provisional": request.provisional,
            "valuation_source": self._db_ledger_validate_optional_text(request.valuation_source),
            "fx_source": self._db_ledger_validate_optional_text(request.fx_source),
            "ingestion_run_id": self._db_ledger_validate_optional_uuid_text(request.ingestion_run_id),
        }

    def _db_ledger_map_snapshot_row(self, row: Any) -> PnlSnapshotDailyRecord:
        """Map SQLAlchemy row to typed daily snapshot record.

        Args:
            row: SQLAlchemy row mapping.

        Returns:
            PnlSnapshotDailyRecord: Typed daily snapshot model.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        return PnlSnapshotDailyRecord(
            pnl_snapshot_daily_id=row["pnl_snapshot_daily_id"],
            account_id=row["account_id"],
            report_date_local=row["report_date_local"],
            instrument_id=row["instrument_id"],
            position_qty=str(row["position_qty"]),
            cost_basis=None if row["cost_basis"] is None else str(row["cost_basis"]),
            realized_pnl=str(row["realized_pnl"]),
            unrealized_pnl=str(row["unrealized_pnl"]),
            total_pnl=str(row["total_pnl"]),
            fees=str(row["fees"]),
            withholding_tax=str(row["withholding_tax"]),
            currency=row["currency"],
            provisional=row["provisional"],
            valuation_source=row["valuation_source"],
            fx_source=row["fx_source"],
            ingestion_run_id=row["ingestion_run_id"],
            created_at_utc=row["created_at_utc"],
        )

    def _db_ledger_validate_non_empty_text(self, value: str, field_name: str) -> str:
        """Validate required text and normalize surrounding whitespace.

        Args:
            value: Candidate text value.
            field_name: Field name for deterministic error text.

        Returns:
            str: Normalized text value.

        Raises:
            ValueError: Raised when value is invalid.
        """

        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")

        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError(f"{field_name} must not be blank")

        return normalized_value

    def _db_ledger_validate_optional_text(self, value: str | None) -> str | None:
        """Validate optional text and normalize surrounding whitespace.

        Args:
            value: Optional text value.

        Returns:
            str | None: Normalized text value or None.

        Raises:
            ValueError: Raised when provided type is invalid.
        """

        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("optional text value must be a string when provided")

        normalized_value = value.strip()
        if not normalized_value:
            return None

        return normalized_value

    def _db_ledger_validate_uuid_text(self, value: str, field_name: str) -> str:
        """Validate required UUID text.

        Args:
            value: UUID text value.
            field_name: Field name for deterministic error text.

        Returns:
            str: Normalized UUID value.

        Raises:
            ValueError: Raised when UUID is invalid.
        """

        normalized_value = self._db_ledger_validate_non_empty_text(value, field_name)
        try:
            parsed_uuid = UUID(normalized_value)
        except ValueError as error:
            raise ValueError(f"{field_name} must be a valid UUID string") from error
        return str(parsed_uuid)

    def _db_ledger_validate_optional_uuid_text(self, value: str | None) -> str | None:
        """Validate optional UUID text.

        Args:
            value: Optional UUID value.

        Returns:
            str | None: Normalized UUID value or None.

        Raises:
            ValueError: Raised when UUID is invalid.
        """

        normalized_value = self._db_ledger_validate_optional_text(value)
        if normalized_value is None:
            return None
        try:
            parsed_uuid = UUID(normalized_value)
        except ValueError as error:
            raise ValueError("optional UUID value must be a valid UUID string") from error
        return str(parsed_uuid)

    def _db_ledger_validate_date_text(self, value: str, field_name: str) -> str:
        """Validate YYYY-MM-DD date text input.

        Args:
            value: Date text input.
            field_name: Field name for deterministic error text.

        Returns:
            str: Normalized date text in YYYY-MM-DD format.

        Raises:
            ValueError: Raised when value is invalid.
        """

        normalized_value = self._db_ledger_validate_non_empty_text(value, field_name)
        try:
            parsed_date = date.fromisoformat(normalized_value)
        except ValueError as error:
            raise ValueError(f"{field_name} must be a valid YYYY-MM-DD date string") from error
        return parsed_date.isoformat()

    def _db_ledger_validate_optional_date_text(self, value: str | None, field_name: str) -> str | None:
        """Validate optional YYYY-MM-DD date text input.

        Args:
            value: Optional date text input.
            field_name: Field name for deterministic error text.

        Returns:
            str | None: Normalized date text or None.

        Raises:
            ValueError: Raised when value is invalid.
        """

        normalized_value = self._db_ledger_validate_optional_text(value)
        if normalized_value is None:
            return None

        try:
            parsed_date = date.fromisoformat(normalized_value)
        except ValueError as error:
            raise ValueError(f"{field_name} must be a valid YYYY-MM-DD date string") from error
        return parsed_date.isoformat()


__all__ = ["SQLAlchemyLedgerSnapshotService"]
