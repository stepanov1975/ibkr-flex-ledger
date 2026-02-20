"""Database service for Task 7 ledger inputs and snapshot persistence."""
# pylint: disable=duplicate-code

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.db.interfaces import (
    LedgerCashflowRecord,
    LedgerSnapshotRepositoryPort,
    LedgerTradeFillRecord,
    PnlSnapshotDailyRecord,
    PnlSnapshotDailyUpsertRequest,
    PositionLotUpsertRequest,
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

    def db_ledger_trade_fill_list_for_account(
        self,
        account_id: str,
        through_report_date_local: str | None = None,
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

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT "
                        "event_trade_fill_id, account_id, instrument_id, source_raw_record_id, trade_timestamp_utc, "
                        "report_date_local, side, quantity, price, fees, commission, functional_currency "
                        "FROM event_trade_fill "
                        "WHERE account_id = :account_id "
                        "AND (CAST(:through_report_date_local AS date) IS NULL "
                        "OR report_date_local <= CAST(:through_report_date_local AS date)) "
                        "ORDER BY trade_timestamp_utc asc, source_raw_record_id asc, event_trade_fill_id asc"
                    ),
                    {
                        "account_id": normalized_account_id,
                        "through_report_date_local": normalized_through_date,
                    },
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
            )
            for row in rows
        ]

    def db_ledger_cashflow_list_for_account(
        self,
        account_id: str,
        through_report_date_local: str | None = None,
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

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT "
                        "event_cashflow_id, account_id, instrument_id, report_date_local, withholding_tax, fees, functional_currency "
                        "FROM event_cashflow "
                        "WHERE account_id = :account_id "
                        "AND (CAST(:through_report_date_local AS date) IS NULL "
                        "OR report_date_local <= CAST(:through_report_date_local AS date)) "
                        "ORDER BY report_date_local asc, event_cashflow_id asc"
                    ),
                    {
                        "account_id": normalized_account_id,
                        "through_report_date_local": normalized_through_date,
                    },
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
            )
            for row in rows
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
                        "total_pnl, fees, withholding_tax, currency, provisional, valuation_source, fx_source, ingestion_run_id"
                        ") VALUES ("
                        ":account_id, CAST(:report_date_local AS date), CAST(:instrument_id AS uuid), "
                        "CAST(:position_qty AS numeric), CAST(:cost_basis AS numeric), CAST(:realized_pnl AS numeric), "
                        "CAST(:unrealized_pnl AS numeric), CAST(:total_pnl AS numeric), CAST(:fees AS numeric), "
                        "CAST(:withholding_tax AS numeric), :currency, :provisional, :valuation_source, :fx_source, "
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
                        "provisional = EXCLUDED.provisional, "
                        "valuation_source = EXCLUDED.valuation_source, "
                        "fx_source = EXCLUDED.fx_source, "
                        "ingestion_run_id = EXCLUDED.ingestion_run_id"
                    ),
                    normalized_requests,
                )
        except SQLAlchemyError as error:
            raise RuntimeError("daily snapshot upsert failed") from error

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
