"""Database service for canonical mapping reads and UPSERT persistence."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.db.interfaces import (
    CanonicalCashflowUpsertRequest,
    CanonicalCorpActionUpsertRequest,
    CanonicalFxUpsertRequest,
    CanonicalInstrumentRecord,
    CanonicalInstrumentUpsertRequest,
    CanonicalPersistenceRepositoryPort,
    CanonicalTradeFillUpsertRequest,
    RawRecordForCanonicalMapping,
    RawRecordReadRepositoryPort,
)


class SQLAlchemyCanonicalPersistenceService(CanonicalPersistenceRepositoryPort, RawRecordReadRepositoryPort):
    """SQLAlchemy implementation of canonical mapping read and UPSERT operations."""

    def __init__(self, engine: Engine):
        """Initialize canonical persistence service.

        Args:
            engine: SQLAlchemy engine used for all persistence operations.

        Returns:
            None: Initializer does not return values.

        Raises:
            ValueError: Raised when engine is invalid.
        """

        if engine is None:
            raise ValueError("engine must not be None")

        self._engine = engine

    def db_raw_record_list_for_period(
        self,
        account_id: str,
        period_key: str,
        flex_query_id: str,
    ) -> list[RawRecordForCanonicalMapping]:
        """List raw rows for one account/period/query identity.

        Args:
            account_id: Internal account identifier.
            period_key: Ingestion period identity key.
            flex_query_id: Upstream Flex query id.

        Returns:
            list[RawRecordForCanonicalMapping]: Deterministically ordered raw rows.

        Raises:
            ValueError: Raised when input values are invalid.
            RuntimeError: Raised when read operation fails.
        """

        normalized_account_id = self._db_canonical_validate_non_empty_text(account_id, "account_id")
        normalized_period_key = self._db_canonical_validate_non_empty_text(period_key, "period_key")
        normalized_flex_query_id = self._db_canonical_validate_non_empty_text(flex_query_id, "flex_query_id")

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT raw_record_id, ingestion_run_id, account_id, period_key, flex_query_id, "
                        "report_date_local, section_name, source_row_ref, source_payload "
                        "FROM raw_record "
                        "WHERE account_id = :account_id AND period_key = :period_key AND flex_query_id = :flex_query_id "
                        "ORDER BY created_at_utc ASC, raw_record_id ASC"
                    ),
                    {
                        "account_id": normalized_account_id,
                        "period_key": normalized_period_key,
                        "flex_query_id": normalized_flex_query_id,
                    },
                ).mappings().all()
        except SQLAlchemyError as error:
            raise RuntimeError("canonical raw row read failed") from error

        return [
            RawRecordForCanonicalMapping(
                raw_record_id=row["raw_record_id"],
                ingestion_run_id=row["ingestion_run_id"],
                account_id=row["account_id"],
                period_key=row["period_key"],
                flex_query_id=row["flex_query_id"],
                report_date_local=row["report_date_local"],
                section_name=row["section_name"],
                source_row_ref=row["source_row_ref"],
                source_payload=dict(row["source_payload"]),
            )
            for row in rows
        ]

    def db_canonical_instrument_upsert(self, request: CanonicalInstrumentUpsertRequest) -> CanonicalInstrumentRecord:
        """Persist or reuse canonical instrument by conid-first identity.

        Args:
            request: Instrument upsert request.

        Returns:
            CanonicalInstrumentRecord: Persisted canonical instrument record.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence operation fails.
        """

        normalized_request = self._db_canonical_validate_instrument_request(request)

        try:
            with self._engine.begin() as connection:
                row = connection.execute(
                    text(
                        "INSERT INTO instrument ("
                        "account_id, conid, symbol, local_symbol, isin, cusip, figi, asset_category, currency, description"
                        ") VALUES ("
                        ":account_id, :conid, :symbol, :local_symbol, :isin, :cusip, :figi, :asset_category, :currency, :description"
                        ") ON CONFLICT (account_id, conid) DO UPDATE SET "
                        "symbol = EXCLUDED.symbol, "
                        "local_symbol = COALESCE(EXCLUDED.local_symbol, instrument.local_symbol), "
                        "isin = COALESCE(EXCLUDED.isin, instrument.isin), "
                        "cusip = COALESCE(EXCLUDED.cusip, instrument.cusip), "
                        "figi = COALESCE(EXCLUDED.figi, instrument.figi), "
                        "asset_category = EXCLUDED.asset_category, "
                        "currency = EXCLUDED.currency, "
                        "description = COALESCE(EXCLUDED.description, instrument.description), "
                        "updated_at_utc = now() "
                        "RETURNING instrument_id, account_id, conid"
                    ),
                    {
                        "account_id": normalized_request.account_id,
                        "conid": normalized_request.conid,
                        "symbol": normalized_request.symbol,
                        "local_symbol": normalized_request.local_symbol,
                        "isin": normalized_request.isin,
                        "cusip": normalized_request.cusip,
                        "figi": normalized_request.figi,
                        "asset_category": normalized_request.asset_category,
                        "currency": normalized_request.currency,
                        "description": normalized_request.description,
                    },
                ).mappings().one()
        except SQLAlchemyError as error:
            raise RuntimeError("canonical instrument upsert failed") from error

        return CanonicalInstrumentRecord(
            instrument_id=row["instrument_id"],
            account_id=row["account_id"],
            conid=row["conid"],
        )

    def db_canonical_trade_fill_upsert(self, request: CanonicalTradeFillUpsertRequest) -> None:
        """UPSERT one canonical trade-fill event by frozen natural key.

        Args:
            request: Canonical trade-fill upsert request.

        Returns:
            None: Event upsert is persisted as a side effect.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence operation fails.
        """

        normalized_request = self._db_canonical_validate_trade_request(request)

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO event_trade_fill ("
                        "account_id, instrument_id, ingestion_run_id, source_raw_record_id, ib_exec_id, transaction_id, "
                        "trade_timestamp_utc, report_date_local, side, quantity, price, cost, commission, fees, realized_pnl, "
                        "net_cash, net_cash_in_base, fx_rate_to_base, currency, functional_currency"
                        ") VALUES ("
                        ":account_id, :instrument_id::uuid, :ingestion_run_id::uuid, :source_raw_record_id::uuid, :ib_exec_id, "
                        ":transaction_id, :trade_timestamp_utc::timestamptz, :report_date_local::date, :side, "
                        ":quantity::numeric, :price::numeric, :cost::numeric, :commission::numeric, :fees::numeric, "
                        ":realized_pnl::numeric, :net_cash::numeric, :net_cash_in_base::numeric, :fx_rate_to_base::numeric, "
                        ":currency, :functional_currency"
                        ") ON CONFLICT ON CONSTRAINT uq_event_trade_fill_account_exec DO UPDATE SET "
                        "commission = EXCLUDED.commission, "
                        "realized_pnl = EXCLUDED.realized_pnl, "
                        "net_cash = EXCLUDED.net_cash, "
                        "cost = EXCLUDED.cost"
                    ),
                    normalized_request,
                )
        except SQLAlchemyError as error:
            raise RuntimeError("canonical trade fill upsert failed") from error

    def db_canonical_cashflow_upsert(self, request: CanonicalCashflowUpsertRequest) -> None:
        """UPSERT one canonical cashflow event by frozen natural key.

        Args:
            request: Canonical cashflow upsert request.

        Returns:
            None: Event upsert is persisted as a side effect.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence operation fails.
        """

        normalized_request = self._db_canonical_validate_cashflow_request(request)

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO event_cashflow ("
                        "account_id, instrument_id, ingestion_run_id, source_raw_record_id, transaction_id, cash_action, "
                        "report_date_local, effective_at_utc, amount, amount_in_base, currency, functional_currency, "
                        "withholding_tax, fees, is_correction"
                        ") VALUES ("
                        ":account_id, :instrument_id::uuid, :ingestion_run_id::uuid, :source_raw_record_id::uuid, :transaction_id, "
                        ":cash_action, :report_date_local::date, :effective_at_utc::timestamptz, :amount::numeric, "
                        ":amount_in_base::numeric, :currency, :functional_currency, :withholding_tax::numeric, :fees::numeric, false"
                        ") ON CONFLICT ON CONSTRAINT uq_event_cashflow_account_txn_action_ccy DO UPDATE SET "
                        "ingestion_run_id = EXCLUDED.ingestion_run_id, "
                        "source_raw_record_id = EXCLUDED.source_raw_record_id, "
                        "instrument_id = COALESCE(EXCLUDED.instrument_id, event_cashflow.instrument_id), "
                        "report_date_local = EXCLUDED.report_date_local, "
                        "effective_at_utc = EXCLUDED.effective_at_utc, "
                        "amount = EXCLUDED.amount, "
                        "amount_in_base = EXCLUDED.amount_in_base, "
                        "withholding_tax = EXCLUDED.withholding_tax, "
                        "fees = EXCLUDED.fees, "
                        "is_correction = event_cashflow.is_correction "
                        "OR event_cashflow.amount IS DISTINCT FROM EXCLUDED.amount "
                        "OR event_cashflow.report_date_local IS DISTINCT FROM EXCLUDED.report_date_local"
                    ),
                    normalized_request,
                )
        except SQLAlchemyError as error:
            raise RuntimeError("canonical cashflow upsert failed") from error

    def db_canonical_fx_upsert(self, request: CanonicalFxUpsertRequest) -> None:
        """UPSERT one canonical FX event by frozen natural key.

        Args:
            request: Canonical FX upsert request.

        Returns:
            None: Event upsert is persisted as a side effect.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence operation fails.
        """

        normalized_request = self._db_canonical_validate_fx_request(request)

        try:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO event_fx ("
                        "account_id, ingestion_run_id, source_raw_record_id, transaction_id, report_date_local, currency, "
                        "functional_currency, fx_rate, fx_source, provisional, diagnostic_code"
                        ") VALUES ("
                        ":account_id, :ingestion_run_id::uuid, :source_raw_record_id::uuid, :transaction_id, "
                        ":report_date_local::date, :currency, :functional_currency, :fx_rate::numeric, :fx_source, "
                        ":provisional, :diagnostic_code"
                        ") ON CONFLICT ON CONSTRAINT uq_event_fx_account_txn_ccy_pair DO UPDATE SET "
                        "report_date_local = EXCLUDED.report_date_local, "
                        "fx_rate = EXCLUDED.fx_rate, "
                        "fx_source = EXCLUDED.fx_source, "
                        "provisional = EXCLUDED.provisional, "
                        "diagnostic_code = EXCLUDED.diagnostic_code"
                    ),
                    normalized_request,
                )
        except SQLAlchemyError as error:
            raise RuntimeError("canonical fx upsert failed") from error

    def db_canonical_corp_action_upsert(self, request: CanonicalCorpActionUpsertRequest) -> None:
        """UPSERT one canonical corporate-action event by frozen natural key.

        Args:
            request: Canonical corporate-action upsert request.

        Returns:
            None: Event upsert is persisted as a side effect.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence operation fails.
        """

        normalized_request = self._db_canonical_validate_corp_action_request(request)

        try:
            with self._engine.begin() as connection:
                if normalized_request["action_id"] is None:
                    connection.execute(
                        text(
                            "INSERT INTO event_corp_action ("
                            "account_id, instrument_id, conid, ingestion_run_id, source_raw_record_id, action_id, "
                            "transaction_id, reorg_code, report_date_local, description, requires_manual, provisional, manual_case_id"
                            ") VALUES ("
                            ":account_id, :instrument_id::uuid, :conid, :ingestion_run_id::uuid, :source_raw_record_id::uuid, "
                            ":action_id, :transaction_id, :reorg_code, :report_date_local::date, :description, "
                            ":requires_manual, :provisional, :manual_case_id::uuid"
                            ") ON CONFLICT ON CONSTRAINT uq_event_corp_action_fallback DO UPDATE SET "
                            "requires_manual = true, "
                            "provisional = true, "
                            "description = COALESCE(EXCLUDED.description, event_corp_action.description), "
                            "manual_case_id = COALESCE(event_corp_action.manual_case_id, EXCLUDED.manual_case_id)"
                        ),
                        normalized_request,
                    )
                    return

                connection.execute(
                    text(
                        "INSERT INTO event_corp_action ("
                        "account_id, instrument_id, conid, ingestion_run_id, source_raw_record_id, action_id, "
                        "transaction_id, reorg_code, report_date_local, description, requires_manual, provisional, manual_case_id"
                        ") VALUES ("
                        ":account_id, :instrument_id::uuid, :conid, :ingestion_run_id::uuid, :source_raw_record_id::uuid, "
                        ":action_id, :transaction_id, :reorg_code, :report_date_local::date, :description, "
                        ":requires_manual, :provisional, :manual_case_id::uuid"
                        ") ON CONFLICT ON CONSTRAINT uq_event_corp_action_account_action DO UPDATE SET "
                        "instrument_id = COALESCE(EXCLUDED.instrument_id, event_corp_action.instrument_id), "
                        "transaction_id = COALESCE(EXCLUDED.transaction_id, event_corp_action.transaction_id), "
                        "reorg_code = EXCLUDED.reorg_code, "
                        "report_date_local = EXCLUDED.report_date_local, "
                        "description = COALESCE(EXCLUDED.description, event_corp_action.description), "
                        "requires_manual = EXCLUDED.requires_manual, "
                        "provisional = EXCLUDED.provisional, "
                        "manual_case_id = COALESCE(EXCLUDED.manual_case_id, event_corp_action.manual_case_id)"
                    ),
                    normalized_request,
                )
        except SQLAlchemyError as error:
            raise RuntimeError("canonical corporate action upsert failed") from error

    def _db_canonical_validate_instrument_request(self, request: CanonicalInstrumentUpsertRequest) -> CanonicalInstrumentUpsertRequest:
        """Validate canonical instrument upsert request values.

        Args:
            request: Canonical instrument upsert request.

        Returns:
            CanonicalInstrumentUpsertRequest: Normalized request payload.

        Raises:
            ValueError: Raised when request is invalid.
        """

        if request is None:
            raise ValueError("request must not be None")

        return CanonicalInstrumentUpsertRequest(
            account_id=self._db_canonical_validate_non_empty_text(request.account_id, "request.account_id"),
            conid=self._db_canonical_validate_non_empty_text(request.conid, "request.conid"),
            symbol=self._db_canonical_validate_non_empty_text(request.symbol, "request.symbol"),
            local_symbol=self._db_canonical_validate_optional_text(request.local_symbol),
            isin=self._db_canonical_validate_optional_text(request.isin),
            cusip=self._db_canonical_validate_optional_text(request.cusip),
            figi=self._db_canonical_validate_optional_text(request.figi),
            asset_category=self._db_canonical_validate_non_empty_text(request.asset_category, "request.asset_category"),
            currency=self._db_canonical_validate_non_empty_text(request.currency, "request.currency"),
            description=self._db_canonical_validate_optional_text(request.description),
        )

    def _db_canonical_validate_trade_request(self, request: CanonicalTradeFillUpsertRequest) -> dict[str, Any]:
        """Validate canonical trade upsert request values.

        Args:
            request: Canonical trade upsert request.

        Returns:
            dict[str, Any]: SQL-ready request parameters.

        Raises:
            ValueError: Raised when request is invalid.
        """

        if request is None:
            raise ValueError("request must not be None")

        return {
            "account_id": self._db_canonical_validate_non_empty_text(request.account_id, "request.account_id"),
            "instrument_id": self._db_canonical_validate_uuid_text(request.instrument_id, "request.instrument_id"),
            "ingestion_run_id": self._db_canonical_validate_uuid_text(request.ingestion_run_id, "request.ingestion_run_id"),
            "source_raw_record_id": self._db_canonical_validate_uuid_text(
                request.source_raw_record_id,
                "request.source_raw_record_id",
            ),
            "ib_exec_id": self._db_canonical_validate_non_empty_text(request.ib_exec_id, "request.ib_exec_id"),
            "transaction_id": self._db_canonical_validate_optional_text(request.transaction_id),
            "trade_timestamp_utc": self._db_canonical_validate_non_empty_text(
                request.trade_timestamp_utc,
                "request.trade_timestamp_utc",
            ),
            "report_date_local": self._db_canonical_validate_non_empty_text(
                request.report_date_local,
                "request.report_date_local",
            ),
            "side": self._db_canonical_validate_non_empty_text(request.side, "request.side"),
            "quantity": self._db_canonical_validate_non_empty_text(request.quantity, "request.quantity"),
            "price": self._db_canonical_validate_non_empty_text(request.price, "request.price"),
            "cost": request.cost,
            "commission": request.commission,
            "fees": request.fees,
            "realized_pnl": request.realized_pnl,
            "net_cash": request.net_cash,
            "net_cash_in_base": request.net_cash_in_base,
            "fx_rate_to_base": request.fx_rate_to_base,
            "currency": self._db_canonical_validate_non_empty_text(request.currency, "request.currency"),
            "functional_currency": self._db_canonical_validate_non_empty_text(
                request.functional_currency,
                "request.functional_currency",
            ),
        }

    def _db_canonical_validate_cashflow_request(self, request: CanonicalCashflowUpsertRequest) -> dict[str, Any]:
        """Validate canonical cashflow upsert request values.

        Args:
            request: Canonical cashflow upsert request.

        Returns:
            dict[str, Any]: SQL-ready request parameters.

        Raises:
            ValueError: Raised when request is invalid.
        """

        if request is None:
            raise ValueError("request must not be None")

        return {
            "account_id": self._db_canonical_validate_non_empty_text(request.account_id, "request.account_id"),
            "instrument_id": self._db_canonical_validate_optional_uuid_text(request.instrument_id),
            "ingestion_run_id": self._db_canonical_validate_uuid_text(request.ingestion_run_id, "request.ingestion_run_id"),
            "source_raw_record_id": self._db_canonical_validate_uuid_text(
                request.source_raw_record_id,
                "request.source_raw_record_id",
            ),
            "transaction_id": self._db_canonical_validate_non_empty_text(request.transaction_id, "request.transaction_id"),
            "cash_action": self._db_canonical_validate_non_empty_text(request.cash_action, "request.cash_action"),
            "report_date_local": self._db_canonical_validate_non_empty_text(
                request.report_date_local,
                "request.report_date_local",
            ),
            "effective_at_utc": request.effective_at_utc,
            "amount": self._db_canonical_validate_non_empty_text(request.amount, "request.amount"),
            "amount_in_base": request.amount_in_base,
            "currency": self._db_canonical_validate_non_empty_text(request.currency, "request.currency"),
            "functional_currency": self._db_canonical_validate_non_empty_text(
                request.functional_currency,
                "request.functional_currency",
            ),
            "withholding_tax": request.withholding_tax,
            "fees": request.fees,
        }

    def _db_canonical_validate_fx_request(self, request: CanonicalFxUpsertRequest) -> dict[str, Any]:
        """Validate canonical FX upsert request values.

        Args:
            request: Canonical FX upsert request.

        Returns:
            dict[str, Any]: SQL-ready request parameters.

        Raises:
            ValueError: Raised when request is invalid.
        """

        if request is None:
            raise ValueError("request must not be None")

        return {
            "account_id": self._db_canonical_validate_non_empty_text(request.account_id, "request.account_id"),
            "ingestion_run_id": self._db_canonical_validate_uuid_text(request.ingestion_run_id, "request.ingestion_run_id"),
            "source_raw_record_id": self._db_canonical_validate_uuid_text(
                request.source_raw_record_id,
                "request.source_raw_record_id",
            ),
            "transaction_id": self._db_canonical_validate_non_empty_text(request.transaction_id, "request.transaction_id"),
            "report_date_local": self._db_canonical_validate_non_empty_text(
                request.report_date_local,
                "request.report_date_local",
            ),
            "currency": self._db_canonical_validate_non_empty_text(request.currency, "request.currency"),
            "functional_currency": self._db_canonical_validate_non_empty_text(
                request.functional_currency,
                "request.functional_currency",
            ),
            "fx_rate": request.fx_rate,
            "fx_source": self._db_canonical_validate_non_empty_text(request.fx_source, "request.fx_source"),
            "provisional": request.provisional,
            "diagnostic_code": self._db_canonical_validate_optional_text(request.diagnostic_code),
        }

    def _db_canonical_validate_corp_action_request(self, request: CanonicalCorpActionUpsertRequest) -> dict[str, Any]:
        """Validate canonical corporate-action upsert request values.

        Args:
            request: Canonical corporate-action upsert request.

        Returns:
            dict[str, Any]: SQL-ready request parameters.

        Raises:
            ValueError: Raised when request is invalid.
        """

        if request is None:
            raise ValueError("request must not be None")

        return {
            "account_id": self._db_canonical_validate_non_empty_text(request.account_id, "request.account_id"),
            "instrument_id": self._db_canonical_validate_optional_uuid_text(request.instrument_id),
            "conid": self._db_canonical_validate_non_empty_text(request.conid, "request.conid"),
            "ingestion_run_id": self._db_canonical_validate_uuid_text(request.ingestion_run_id, "request.ingestion_run_id"),
            "source_raw_record_id": self._db_canonical_validate_uuid_text(
                request.source_raw_record_id,
                "request.source_raw_record_id",
            ),
            "action_id": self._db_canonical_validate_optional_text(request.action_id),
            "transaction_id": self._db_canonical_validate_optional_text(request.transaction_id),
            "reorg_code": self._db_canonical_validate_non_empty_text(request.reorg_code, "request.reorg_code"),
            "report_date_local": self._db_canonical_validate_non_empty_text(
                request.report_date_local,
                "request.report_date_local",
            ),
            "description": self._db_canonical_validate_optional_text(request.description),
            "requires_manual": request.requires_manual,
            "provisional": request.provisional,
            "manual_case_id": self._db_canonical_validate_optional_uuid_text(request.manual_case_id),
        }

    def _db_canonical_validate_non_empty_text(self, value: str, field_name: str) -> str:
        """Validate non-empty text values.

        Args:
            value: Input text value.
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

    def _db_canonical_validate_optional_text(self, value: str | None) -> str | None:
        """Validate optional text values.

        Args:
            value: Optional input text.

        Returns:
            str | None: Normalized text or None.

        Raises:
            ValueError: Raised when text value type is invalid.
        """

        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("optional text value must be a string when provided")

        normalized_value = value.strip()
        if not normalized_value:
            return None

        return normalized_value

    def _db_canonical_validate_uuid_text(self, value: str, field_name: str) -> str:
        """Validate UUID text values.

        Args:
            value: UUID text input.
            field_name: Field name for deterministic error text.

        Returns:
            str: Normalized UUID text.

        Raises:
            ValueError: Raised when UUID text is invalid.
        """

        normalized_value = self._db_canonical_validate_non_empty_text(value, field_name)
        if len(normalized_value) != 36:
            raise ValueError(f"{field_name} must be a UUID string")
        return normalized_value

    def _db_canonical_validate_optional_uuid_text(self, value: str | None) -> str | None:
        """Validate optional UUID text values.

        Args:
            value: Optional UUID text.

        Returns:
            str | None: Normalized UUID text or None.

        Raises:
            ValueError: Raised when UUID text is invalid.
        """

        normalized_value = self._db_canonical_validate_optional_text(value)
        if normalized_value is None:
            return None
        if len(normalized_value) != 36:
            raise ValueError("optional UUID value must be a UUID string")
        return normalized_value


__all__ = [
    "CanonicalTradeFillUpsertRequest",
    "CanonicalCashflowUpsertRequest",
    "CanonicalFxUpsertRequest",
    "CanonicalCorpActionUpsertRequest",
    "SQLAlchemyCanonicalPersistenceService",
]
