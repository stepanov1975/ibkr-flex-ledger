"""Canonical mapping service for Task 5 raw-to-canonical transformations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import date

from app.domain.flex_parsing import (
    domain_flex_normalize_optional_text,
    domain_flex_parse_local_date,
    domain_flex_parse_timestamp_to_utc_iso,
)
from app.domain import domain_classify_corporate_action
from app.db.interfaces import (
    CanonicalCashflowUpsertRequest,
    CanonicalCorpActionUpsertRequest,
    CanonicalFxUpsertRequest,
    CanonicalInstrumentUpsertRequest,
    CanonicalTradeFillUpsertRequest,
)

from .interfaces import CanonicalMappingBatch, MappingContractViolationError, RawRecordForMapping


@dataclass(frozen=True)
class MappingServiceConfig:
    """Configuration for canonical mapping behavior.

    Attributes:
        default_asset_category: Fallback asset category when source payload omits it.
    """

    default_asset_category: str = "STK"

    def mapping_default_asset_category(self) -> str:
        """Return the configured fallback asset category.

        Returns:
            str: Fallback asset category.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        return self.default_asset_category

    def mapping_validate(self) -> None:
        """Validate mapping configuration values.

        Returns:
            None: This method does not return a value.

        Raises:
            ValueError: Raised when configured defaults are invalid.
        """

        if not self.default_asset_category.strip():
            raise ValueError("config.default_asset_category must not be blank")


class CanonicalMappingService:
    """Concrete mapping service for canonical event transformations."""

    def __init__(self, config: MappingServiceConfig | None = None):
        """Initialize canonical mapping service.

        Args:
            config: Optional mapping configuration values.

        Returns:
            None: Initializer does not return values.

        Raises:
            ValueError: Raised when config values are invalid.
        """

        resolved_config = config or MappingServiceConfig()
        resolved_config.mapping_validate()

        self._config = resolved_config

    def mapping_contract_version(self) -> str:
        """Return mapping contract version identifier.

        Returns:
            str: Mapping contract version string.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        return "v1"

    def mapping_build_canonical_batch(
        self,
        account_id: str,
        functional_currency: str,
        raw_records: list[RawRecordForMapping],
    ) -> CanonicalMappingBatch:
        """Map raw rows into canonical event UPSERT requests.

        Args:
            account_id: Internal account context identifier.
            functional_currency: Functional/base reporting currency code.
            raw_records: Raw rows to map.

        Returns:
            CanonicalMappingBatch: Grouped canonical event upsert requests.

        Raises:
            MappingContractViolationError: Raised when one row violates required mapping contract.
            ValueError: Raised when top-level input values are invalid.
        """

        normalized_account_id = self._mapping_validate_non_empty_text(account_id, "account_id")
        normalized_functional_currency = self._mapping_validate_non_empty_text(functional_currency, "functional_currency")

        instrument_requests: list[CanonicalInstrumentUpsertRequest] = []
        trade_requests: list[CanonicalTradeFillUpsertRequest] = []
        cashflow_requests: list[CanonicalCashflowUpsertRequest] = []
        fx_requests: list[CanonicalFxUpsertRequest] = []
        corp_action_requests: list[CanonicalCorpActionUpsertRequest] = []

        for raw_record in raw_records:
            section_name = raw_record.section_name.strip()
            if section_name == "Trades":
                if not self._mapping_row_matches_tag(raw_record, expected_row_tag="Trade"):
                    continue
                trade_identity = self._mapping_resolve_trade_identity(raw_record)
                if trade_identity is None:
                    continue
                instrument_request, trade_request = self._mapping_map_trade_record(
                    account_id=normalized_account_id,
                    functional_currency=normalized_functional_currency,
                    raw_record=raw_record,
                    trade_identity=trade_identity,
                )
                instrument_requests.append(instrument_request)
                trade_requests.append(trade_request)
                continue

            if section_name == "OpenPositions":
                if not self._mapping_row_matches_tag(raw_record, expected_row_tag="OpenPosition"):
                    continue
                open_position_instrument_request = self._mapping_map_open_position_instrument(
                    raw_record=raw_record,
                    account_id=normalized_account_id,
                )
                if open_position_instrument_request is not None:
                    instrument_requests.append(open_position_instrument_request)
                continue

            if section_name == "CashTransactions":
                if not self._mapping_row_matches_tag(raw_record, expected_row_tag="CashTransaction"):
                    continue
                cashflow_instrument_request, cashflow_request = self._mapping_map_cashflow_record(
                    account_id=normalized_account_id,
                    functional_currency=normalized_functional_currency,
                    raw_record=raw_record,
                )
                if cashflow_instrument_request is not None:
                    instrument_requests.append(cashflow_instrument_request)
                cashflow_requests.append(cashflow_request)
                continue

            if section_name == "ConversionRates":
                if not self._mapping_row_matches_tag(raw_record, expected_row_tag="ConversionRate"):
                    continue
                fx_requests.append(
                    self._mapping_map_fx_record(
                        account_id=normalized_account_id,
                        functional_currency=normalized_functional_currency,
                        raw_record=raw_record,
                    )
                )
                continue

            if section_name == "CorporateActions":
                if not self._mapping_row_matches_tag(raw_record, expected_row_tag="CorporateAction"):
                    continue
                corp_action_instrument_request, corp_action_request = self._mapping_map_corp_action_record(
                    account_id=normalized_account_id,
                    raw_record=raw_record,
                )
                if corp_action_instrument_request is not None:
                    instrument_requests.append(corp_action_instrument_request)
                corp_action_requests.append(corp_action_request)

        return CanonicalMappingBatch(
            instrument_upsert_requests=tuple(instrument_requests),
            trade_fill_requests=tuple(trade_requests),
            cashflow_requests=tuple(cashflow_requests),
            fx_requests=tuple(fx_requests),
            corp_action_requests=tuple(corp_action_requests),
        )

    def _mapping_row_matches_tag(self, raw_record: RawRecordForMapping, expected_row_tag: str) -> bool:
        """Check whether `source_row_ref` encodes one expected row tag for a section.

        Args:
            raw_record: Raw row payload for routing.
            expected_row_tag: Expected row element tag.

        Returns:
            bool: True when row tag is present and matches expected tag.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        parsed_row_tag = self._mapping_extract_row_tag(raw_record)
        return parsed_row_tag == expected_row_tag

    def _mapping_extract_row_tag(self, raw_record: RawRecordForMapping) -> str | None:
        """Extract row tag from deterministic `source_row_ref` format.

        Args:
            raw_record: Raw row payload for routing.

        Returns:
            str | None: Parsed row tag when section/tag prefix is valid; otherwise None.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        section_name = raw_record.section_name.strip()
        source_row_ref = raw_record.source_row_ref.strip()
        source_row_ref_parts = source_row_ref.split(":", 2)
        if len(source_row_ref_parts) < 2:
            return None

        parsed_section_name = source_row_ref_parts[0].strip()
        parsed_row_tag = source_row_ref_parts[1].strip()
        if not parsed_section_name or not parsed_row_tag:
            return None
        if parsed_section_name != section_name:
            return None
        return parsed_row_tag

    def _mapping_map_trade_record(
        self,
        account_id: str,
        functional_currency: str,
        raw_record: RawRecordForMapping,
        trade_identity: str,
    ) -> tuple[CanonicalInstrumentUpsertRequest, CanonicalTradeFillUpsertRequest]:
        """Map one Trades raw row into instrument and trade requests.

        Args:
            account_id: Internal account identifier.
            functional_currency: Functional/base currency code.
            raw_record: Raw row payload.
            trade_identity: Canonical execution identity for the trade.

        Returns:
            tuple[CanonicalInstrumentUpsertRequest, CanonicalTradeFillUpsertRequest]: Instrument and event requests.

        Raises:
            MappingContractViolationError: Raised when required fields are missing.
        """

        payload = raw_record.source_payload
        conid = self._mapping_required_value(payload, "conid", raw_record)
        side = self._mapping_required_value(payload, "buySell", raw_record).upper()
        quantity = self._mapping_required_decimal_value(payload, "quantity", raw_record)
        price = self._mapping_required_decimal_value(payload, "tradePrice", raw_record)
        currency = self._mapping_required_value(payload, "currency", raw_record)
        asset_category = (
            self._mapping_optional_value(payload, "assetCategory")
            or self._config.default_asset_category
        ).upper()
        multiplier = self._mapping_optional_decimal_value(payload, "multiplier", raw_record)
        if multiplier is None and asset_category == "OPT":
            raise MappingContractViolationError(
                "mapping contract violation: multiplier must be positive "
                f"source_row_ref={raw_record.source_row_ref}"
            )
        if multiplier is not None and Decimal(multiplier) <= Decimal("0"):
            raise MappingContractViolationError(
                "mapping contract violation: multiplier must be positive "
                f"source_row_ref={raw_record.source_row_ref}"
            )
        fx_rate_to_base = self._mapping_optional_decimal_value(
            payload,
            "fxRateToBase",
            raw_record,
        )
        if fx_rate_to_base is not None and Decimal(fx_rate_to_base) <= Decimal("0"):
            raise MappingContractViolationError(
                "mapping contract violation: fxRateToBase must be positive "
                f"source_row_ref={raw_record.source_row_ref}"
            )
        trade_timestamp_utc = self._mapping_resolve_trade_timestamp(raw_record)
        report_date_local = self._mapping_resolve_report_date(raw_record, payload)

        instrument_request = CanonicalInstrumentUpsertRequest(
            account_id=account_id,
            conid=conid,
            symbol=self._mapping_optional_value(payload, "symbol") or conid,
            local_symbol=self._mapping_optional_value(payload, "localSymbol"),
            isin=self._mapping_optional_value(payload, "isin"),
            cusip=self._mapping_optional_value(payload, "cusip"),
            figi=self._mapping_optional_value(payload, "figi"),
            asset_category=asset_category,
            currency=currency,
            description=self._mapping_optional_value(payload, "description"),
        )

        trade_request = CanonicalTradeFillUpsertRequest(
            account_id=account_id,
            instrument_id="00000000-0000-0000-0000-000000000000",
            ingestion_run_id=str(raw_record.ingestion_run_id),
            source_raw_record_id=str(raw_record.raw_record_id),
            ib_exec_id=trade_identity,
            transaction_id=self._mapping_optional_value(payload, "transactionID"),
            trade_timestamp_utc=trade_timestamp_utc,
            report_date_local=report_date_local,
            side=side,
            quantity=quantity,
            price=price,
            cost=self._mapping_optional_decimal_value(payload, "cost", raw_record),
            commission=self._mapping_optional_decimal_value(payload, "ibCommission", raw_record),
            fees=self._mapping_optional_decimal_value(payload, "fees", raw_record),
            realized_pnl=self._mapping_optional_decimal_value(payload, "fifoPnlRealized", raw_record),
            net_cash=self._mapping_optional_decimal_value(payload, "netCash", raw_record),
            net_cash_in_base=self._mapping_optional_decimal_value(payload, "netCashInBase", raw_record),
            fx_rate_to_base=fx_rate_to_base,
            currency=currency,
            functional_currency=functional_currency,
        )
        return instrument_request, trade_request

    def _mapping_resolve_trade_identity(
        self,
        raw_record: RawRecordForMapping,
    ) -> str | None:
        """Resolve one canonical identity for a trade row.

        Args:
            raw_record: Raw trade row payload.

        Returns:
            str | None: Execution identity, or None for non-execution rows.

        Raises:
            MappingContractViolationError: Raised when an execution row lacks identity.
        """

        payload = raw_record.source_payload
        ib_exec_id = payload.get("ibExecID")
        if isinstance(ib_exec_id, str) and ib_exec_id.strip():
            return ib_exec_id
        level_of_detail = self._mapping_optional_value(payload, "levelOfDetail")
        if level_of_detail is None or level_of_detail.upper() != "EXECUTION":
            return None
        transaction_id = self._mapping_optional_value(payload, "transactionID")
        if transaction_id is not None:
            return f"FLEX_TXN:{transaction_id}"
        trade_id = self._mapping_optional_value(payload, "tradeID")
        if trade_id is not None:
            return f"FLEX_TRADE:{trade_id}"
        raise MappingContractViolationError(
            "mapping contract violation: execution row missing stable execution identity "
            f"source_row_ref={raw_record.source_row_ref}"
        )

    def _mapping_map_open_position_instrument(
        self,
        raw_record: RawRecordForMapping,
        account_id: str,
    ) -> CanonicalInstrumentUpsertRequest | None:
        """Map one OpenPositions raw row into an optional instrument request.

        Args:
            raw_record: Raw OpenPositions row payload.
            account_id: Internal account identifier.

        Returns:
            CanonicalInstrumentUpsertRequest | None: Instrument request, excluding CASH and FX.

        Raises:
            MappingContractViolationError: Raised when required values are invalid.
        """

        payload = raw_record.source_payload
        asset_category = self._mapping_required_value(
            payload, "assetCategory", raw_record
        ).upper()
        if asset_category in {"CASH", "FX"}:
            return None
        conid = self._mapping_required_value(payload, "conid", raw_record)
        currency = self._mapping_required_value(payload, "currency", raw_record).upper()
        self._mapping_required_decimal_value(payload, "position", raw_record)
        for optional_key in (
            "markPrice",
            "costBasisMoney",
            "fifoPnlUnrealized",
            "fxRateToBase",
            "multiplier",
        ):
            self._mapping_optional_decimal_value(payload, optional_key, raw_record)
        for positive_key in ("fxRateToBase", "multiplier"):
            parsed_value = self._mapping_optional_decimal_value(
                payload, positive_key, raw_record
            )
            if parsed_value is not None and Decimal(parsed_value) <= Decimal("0"):
                raise MappingContractViolationError(
                    f"mapping contract violation: {positive_key} must be positive "
                    f"source_row_ref={raw_record.source_row_ref}"
                )
        return CanonicalInstrumentUpsertRequest(
            account_id=account_id,
            conid=conid,
            symbol=self._mapping_optional_value(payload, "symbol") or conid,
            local_symbol=self._mapping_optional_value(payload, "localSymbol"),
            isin=self._mapping_optional_value(payload, "isin"),
            cusip=self._mapping_optional_value(payload, "cusip"),
            figi=self._mapping_optional_value(payload, "figi"),
            asset_category=asset_category,
            currency=currency,
            description=self._mapping_optional_value(payload, "description"),
        )

    def _mapping_map_cashflow_record(
        self,
        account_id: str,
        functional_currency: str,
        raw_record: RawRecordForMapping,
    ) -> tuple[CanonicalInstrumentUpsertRequest | None, CanonicalCashflowUpsertRequest]:
        """Map one CashTransactions raw row into optional instrument and cashflow requests.

        Args:
            account_id: Internal account identifier.
            functional_currency: Functional/base currency code.
            raw_record: Raw row payload.

        Returns:
            tuple[CanonicalInstrumentUpsertRequest | None, CanonicalCashflowUpsertRequest]: Optional instrument and cashflow.

        Raises:
            MappingContractViolationError: Raised when required fields are missing.
        """

        payload = raw_record.source_payload
        transaction_id = self._mapping_required_value(payload, "transactionID", raw_record)
        cash_action = self._mapping_required_value(payload, "type", raw_record)
        amount = self._mapping_required_decimal_value(payload, "amount", raw_record)
        currency = self._mapping_required_value(payload, "currency", raw_record)

        conid = self._mapping_optional_value(payload, "conid")
        instrument_request = None
        if conid is not None:
            instrument_request = CanonicalInstrumentUpsertRequest(
                account_id=account_id,
                conid=conid,
                symbol=self._mapping_optional_value(payload, "symbol") or conid,
                local_symbol=self._mapping_optional_value(payload, "localSymbol"),
                isin=self._mapping_optional_value(payload, "isin"),
                cusip=self._mapping_optional_value(payload, "cusip"),
                figi=self._mapping_optional_value(payload, "figi"),
                asset_category=self._mapping_optional_value(payload, "assetCategory") or self._config.default_asset_category,
                currency=currency,
                description=self._mapping_optional_value(payload, "description"),
            )

        cashflow_request = CanonicalCashflowUpsertRequest(
            account_id=account_id,
            instrument_id=None,
            ingestion_run_id=str(raw_record.ingestion_run_id),
            source_raw_record_id=str(raw_record.raw_record_id),
            transaction_id=transaction_id,
            cash_action=cash_action,
            report_date_local=self._mapping_resolve_report_date(raw_record, payload),
            effective_at_utc=self._mapping_optional_timestamp_value(payload, "dateTime", raw_record),
            amount=amount,
            amount_in_base=self._mapping_optional_decimal_value(payload, "amountInBase", raw_record),
            currency=currency,
            functional_currency=functional_currency,
            withholding_tax=self._mapping_optional_decimal_value(payload, "withholdingTax", raw_record),
            fees=self._mapping_optional_decimal_value(payload, "fees", raw_record),
        )
        return instrument_request, cashflow_request

    def _mapping_map_fx_record(
        self,
        account_id: str,
        functional_currency: str,
        raw_record: RawRecordForMapping,
    ) -> CanonicalFxUpsertRequest:
        """Map one ConversionRates raw row into an FX event request.

        Args:
            account_id: Internal account identifier.
            functional_currency: Functional/base currency code.
            raw_record: Raw row payload.

        Returns:
            CanonicalFxUpsertRequest: Canonical FX upsert request.

        Raises:
            MappingContractViolationError: Raised when required fields are missing.
        """

        payload = raw_record.source_payload
        currency = self._mapping_required_value(payload, "fromCurrency", raw_record)
        report_date_local = self._mapping_resolve_report_date(raw_record, payload)
        transaction_id = self._mapping_optional_value(payload, "transactionID") or raw_record.source_row_ref
        fx_rate = self._mapping_optional_decimal_value(payload, "rate", raw_record)

        return CanonicalFxUpsertRequest(
            account_id=account_id,
            ingestion_run_id=str(raw_record.ingestion_run_id),
            source_raw_record_id=str(raw_record.raw_record_id),
            transaction_id=transaction_id,
            report_date_local=report_date_local,
            currency=currency,
            functional_currency=self._mapping_optional_value(payload, "toCurrency") or functional_currency,
            fx_rate=fx_rate,
            fx_source="conversion_rates",
            provisional=fx_rate is None,
            diagnostic_code=None if fx_rate is not None else "FX_RATE_MISSING_ALL_SOURCES",
        )

    def _mapping_map_corp_action_record(
        self,
        account_id: str,
        raw_record: RawRecordForMapping,
    ) -> tuple[CanonicalInstrumentUpsertRequest | None, CanonicalCorpActionUpsertRequest]:
        """Map one CorporateActions raw row into optional instrument and corp-action requests.

        Args:
            account_id: Internal account identifier.
            raw_record: Raw row payload.

        Returns:
            tuple[CanonicalInstrumentUpsertRequest | None, CanonicalCorpActionUpsertRequest]: Optional instrument and event.

        Raises:
            MappingContractViolationError: Raised when required fields are missing.
        """

        payload = raw_record.source_payload
        conid = self._mapping_required_value(payload, "conid", raw_record)
        reorg_code = self._mapping_required_value(payload, "type", raw_record)
        classification = domain_classify_corporate_action(reorg_code, payload)
        report_date_local = self._mapping_resolve_report_date(raw_record, payload)
        currency = self._mapping_optional_value(payload, "currency") or "USD"

        instrument_request = CanonicalInstrumentUpsertRequest(
            account_id=account_id,
            conid=conid,
            symbol=self._mapping_optional_value(payload, "symbol") or conid,
            local_symbol=self._mapping_optional_value(payload, "localSymbol"),
            isin=self._mapping_optional_value(payload, "isin"),
            cusip=self._mapping_optional_value(payload, "cusip"),
            figi=self._mapping_optional_value(payload, "figi"),
            asset_category=self._mapping_optional_value(payload, "assetCategory") or self._config.default_asset_category,
            currency=currency,
            description=self._mapping_optional_value(payload, "description"),
        )

        corp_action_request = CanonicalCorpActionUpsertRequest(
            account_id=account_id,
            instrument_id=None,
            conid=conid,
            ingestion_run_id=str(raw_record.ingestion_run_id),
            source_raw_record_id=str(raw_record.raw_record_id),
            action_id=self._mapping_optional_value(payload, "actionID"),
            transaction_id=self._mapping_optional_value(payload, "transactionID"),
            reorg_code=classification.action_type,
            report_date_local=report_date_local,
            description=self._mapping_optional_value(payload, "description"),
            requires_manual=classification.requires_manual,
            provisional=classification.requires_manual,
            manual_case_id=None,
        )
        return instrument_request, corp_action_request

    def _mapping_resolve_trade_timestamp(self, raw_record: RawRecordForMapping) -> str:
        """Resolve trade timestamp in deterministic UTC ISO-8601 format.

        Args:
            raw_record: Raw row payload.

        Returns:
            str: UTC timestamp in ISO-8601 format.

        Raises:
            MappingContractViolationError: Raised when timestamp cannot be resolved.
        """

        payload = raw_record.source_payload
        return self._mapping_required_timestamp_value(payload, "dateTime", raw_record)

    def _mapping_required_timestamp_value(
        self,
        payload: dict[str, object],
        key: str,
        raw_record: RawRecordForMapping,
    ) -> str:
        """Extract required timestamp value and normalize to UTC ISO-8601.

        Args:
            payload: Source payload object.
            key: Required key in source payload.
            raw_record: Raw row metadata for diagnostics.

        Returns:
            str: Normalized timestamp value in UTC ISO-8601 format.

        Raises:
            MappingContractViolationError: Raised when timestamp is missing or invalid.
        """

        value = self._mapping_required_value(payload, key, raw_record)
        parsed_timestamp = self._mapping_try_parse_timestamp_to_utc(value)
        if parsed_timestamp is None:
            raise MappingContractViolationError(
                "mapping contract violation: invalid timestamp field "
                f"{key} for {raw_record.section_name} at {raw_record.source_row_ref}: {value}"
            )
        return parsed_timestamp

    def _mapping_optional_timestamp_value(
        self,
        payload: dict[str, object],
        key: str,
        raw_record: RawRecordForMapping,
    ) -> str | None:
        """Extract optional timestamp value and normalize to UTC ISO-8601.

        Args:
            payload: Source payload object.
            key: Optional key in source payload.
            raw_record: Raw row metadata for diagnostics.

        Returns:
            str | None: Normalized timestamp value or None when missing.

        Raises:
            MappingContractViolationError: Raised when provided timestamp is invalid.
        """

        value = self._mapping_optional_value(payload, key)
        if value is None:
            return None

        parsed_timestamp = self._mapping_try_parse_timestamp_to_utc(value)
        if parsed_timestamp is None:
            raise MappingContractViolationError(
                "mapping contract violation: invalid timestamp field "
                f"{key} for {raw_record.section_name} at {raw_record.source_row_ref}: {value}"
            )
        return parsed_timestamp

    def _mapping_try_parse_timestamp_to_utc(self, value: str) -> str | None:
        """Try parse known Flex timestamp formats and normalize to UTC ISO-8601.

        Args:
            value: Candidate timestamp value.

        Returns:
            str | None: Normalized UTC timestamp when supported, else None.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """
        return domain_flex_parse_timestamp_to_utc_iso(value)

    def _mapping_resolve_report_date(self, raw_record: RawRecordForMapping, payload: dict[str, object]) -> str:
        """Resolve report date in deterministic YYYY-MM-DD format.

        Args:
            raw_record: Raw row payload.
            payload: Source payload object.

        Returns:
            str: Report date string.

        Raises:
            MappingContractViolationError: Raised when report date cannot be resolved.
        """

        payload_report_date = self._mapping_optional_value(payload, "reportDate")
        if payload_report_date is not None:
            parsed_date = self._mapping_try_parse_report_date(payload_report_date)
            if parsed_date is None:
                raise MappingContractViolationError(
                    "mapping contract violation: invalid reportDate format "
                    f"for {raw_record.section_name} at {raw_record.source_row_ref}: {payload_report_date}"
                )
            return parsed_date.isoformat()

        if raw_record.report_date_local is not None:
            return raw_record.report_date_local.isoformat()

        raise MappingContractViolationError(
            f"mapping contract violation: missing report date for {raw_record.section_name} at {raw_record.source_row_ref}"
        )

    def _mapping_try_parse_report_date(self, value: str) -> date | None:
        """Try parse report date across common IBKR date representations.

        Args:
            value: Report date string from source payload.

        Returns:
            date | None: Parsed date or None when format is not supported.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """
        return domain_flex_parse_local_date(value)

    def _mapping_required_value(self, payload: dict[str, object], key: str, raw_record: RawRecordForMapping) -> str:
        """Extract required string value from source payload.

        Args:
            payload: Source payload object.
            key: Required key in source payload.
            raw_record: Raw row metadata for diagnostics.

        Returns:
            str: Normalized non-empty string value.

        Raises:
            MappingContractViolationError: Raised when key is missing or invalid.
        """

        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise MappingContractViolationError(
                "mapping contract violation: "
                f"missing required field {key} for {raw_record.section_name} at {raw_record.source_row_ref}"
            )
        return value.strip()

    def _mapping_optional_value(self, payload: dict[str, object], key: str) -> str | None:
        """Extract optional normalized string value from source payload.

        Args:
            payload: Source payload object.
            key: Optional key in source payload.

        Returns:
            str | None: Normalized string value or None.
        """

        return domain_flex_normalize_optional_text(payload.get(key))

    def _mapping_required_decimal_value(
        self,
        payload: dict[str, object],
        key: str,
        raw_record: RawRecordForMapping,
    ) -> str:
        """Extract required decimal-like text value from source payload.

        Args:
            payload: Source payload object.
            key: Required key in source payload.
            raw_record: Raw row metadata for diagnostics.

        Returns:
            str: Normalized decimal-like text value.

        Raises:
            MappingContractViolationError: Raised when key is missing or not decimal-like.
        """

        value = self._mapping_required_value(payload, key, raw_record)
        normalized_value = self._mapping_normalize_decimal_text(value)
        self._mapping_validate_decimal_value(value=normalized_value, key=key, raw_record=raw_record)
        return normalized_value

    def _mapping_optional_decimal_value(
        self,
        payload: dict[str, object],
        key: str,
        raw_record: RawRecordForMapping,
    ) -> str | None:
        """Extract optional decimal-like text value from source payload.

        Args:
            payload: Source payload object.
            key: Optional key in source payload.
            raw_record: Raw row metadata for diagnostics.

        Returns:
            str | None: Normalized decimal-like text value or None.

        Raises:
            MappingContractViolationError: Raised when provided value is not decimal-like.
        """

        value = self._mapping_optional_value(payload, key)
        if value is None:
            return None
        normalized_value = self._mapping_normalize_decimal_text(value)
        self._mapping_validate_decimal_value(value=normalized_value, key=key, raw_record=raw_record)
        return normalized_value

    def _mapping_normalize_decimal_text(self, value: str) -> str:
        """Normalize Flex decimal-like text before validation.

        Args:
            value: Candidate decimal text value.

        Returns:
            str: Decimal text normalized for deterministic parsing.

        Raises:
            RuntimeError: This helper does not raise runtime errors.
        """

        # FSN[2026-02-20]: ALWAYS strip comma thousands separators before Decimal parsing.
        # Context: IBKR exports may emit numeric fields as locale-formatted text (for example, 1,234.56). | Symptom: Decimal(value) raises ConversionSyntax.
        # Guard: mapping decimal validation runs after normalization and rejects remaining invalid values. | Test: test_mapping_build_canonical_batch_accepts_required_trade_decimal_with_thousands_separator
        return value.replace(",", "")

    def _mapping_validate_decimal_value(self, value: str, key: str, raw_record: RawRecordForMapping) -> None:
        """Validate that a text value is finite decimal content.

        Args:
            value: Candidate decimal text value.
            key: Source payload field key.
            raw_record: Raw row metadata for diagnostics.

        Returns:
            None: Validation result is communicated via exceptions.

        Raises:
            MappingContractViolationError: Raised when decimal value is invalid.
        """

        try:
            parsed_value = Decimal(value)
        except (InvalidOperation, ValueError) as error:
            raise MappingContractViolationError(
                "mapping contract violation: invalid decimal field "
                f"{key} for {raw_record.section_name} at {raw_record.source_row_ref}: {value}"
            ) from error

        if not parsed_value.is_finite():
            raise MappingContractViolationError(
                "mapping contract violation: invalid decimal field "
                f"{key} for {raw_record.section_name} at {raw_record.source_row_ref}: {value}"
            )

    def _mapping_validate_non_empty_text(self, value: str, field_name: str) -> str:
        """Validate top-level non-empty text values.

        Args:
            value: Input value.
            field_name: Field name for deterministic error messages.

        Returns:
            str: Normalized non-empty value.

        Raises:
            ValueError: Raised when value is invalid.
        """

        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")

        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError(f"{field_name} must not be blank")

        return normalized_value


def mapping_build_canonical_batch(
    account_id: str,
    functional_currency: str,
    raw_records: list[RawRecordForMapping],
) -> CanonicalMappingBatch:
    """Map raw rows into canonical event UPSERT requests.

    Args:
        account_id: Internal account context identifier.
        functional_currency: Functional/base reporting currency code.
        raw_records: Raw rows to map.

    Returns:
        CanonicalMappingBatch: Grouped canonical event upsert requests.

    Raises:
        MappingContractViolationError: Raised when one row violates required mapping contract.
        ValueError: Raised when top-level input values are invalid.
    """

    service = CanonicalMappingService()
    return service.mapping_build_canonical_batch(
        account_id=account_id,
        functional_currency=functional_currency,
        raw_records=raw_records,
    )


__all__ = [
    "RawRecordForMapping",
    "CanonicalMappingBatch",
    "MappingContractViolationError",
    "CanonicalMappingService",
    "mapping_build_canonical_batch",
]
