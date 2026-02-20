"""Regression tests for Task 5 canonical mapping pipeline behavior."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.mapping.service import (
    MappingContractViolationError,
    RawRecordForMapping,
    mapping_build_canonical_batch,
)


def test_mapping_build_canonical_batch_maps_all_supported_event_types() -> None:
    """Map trade, cashflow, fx, and corporate action rows in one deterministic pass.

    Returns:
        None: Assertions validate canonical mapping outputs.

    Raises:
        AssertionError: Raised when mapped event payloads are incomplete.
    """

    ingestion_run_id = uuid4()
    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=ingestion_run_id,
            section_name="Trades",
            source_row_ref="Trades:Trade:transactionID=1001",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "ibExecID": "EXEC-1001",
                "transactionID": "1001",
                "conid": "265598",
                "buySell": "BUY",
                "quantity": "10",
                "tradePrice": "100.10",
                "currency": "USD",
                "fxRateToBase": "1",
                "reportDate": "2026-02-14",
                "dateTime": "2026-02-14T15:20:00+00:00",
            },
        ),
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=ingestion_run_id,
            section_name="CashTransactions",
            source_row_ref="CashTransactions:CashTransaction:transactionID=2001",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "transactionID": "2001",
                "type": "DIV",
                "currency": "USD",
                "amount": "12.50",
                "reportDate": "2026-02-14",
                "dateTime": "2026-02-14T10:00:00+00:00",
                "conid": "265598",
            },
        ),
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=ingestion_run_id,
            section_name="ConversionRates",
            source_row_ref="ConversionRates:ConversionRate:idx=1",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "transactionID": "3001",
                "fromCurrency": "EUR",
                "toCurrency": "USD",
                "rate": "1.105",
                "reportDate": "2026-02-14",
            },
        ),
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=ingestion_run_id,
            section_name="CorporateActions",
            source_row_ref="CorporateActions:CorporateAction:actionID=CA-1",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "actionID": "CA-1",
                "transactionID": "4001",
                "conid": "265598",
                "type": "IC",
                "reportDate": "2026-02-14",
            },
        ),
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert len(mapped_batch.trade_fill_requests) == 1
    assert len(mapped_batch.cashflow_requests) == 1
    assert len(mapped_batch.fx_requests) == 1
    assert len(mapped_batch.corp_action_requests) == 1
    assert mapped_batch.trade_fill_requests[0].ib_exec_id == "EXEC-1001"


def test_mapping_build_canonical_batch_fails_fast_on_contract_violation() -> None:
    """Fail the entire mapping pass when one required canonical field is missing.

    Returns:
        None: Assertions validate fail-fast behavior.

    Raises:
        AssertionError: Raised when contract violation is not raised.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="Trades",
            source_row_ref="Trades:Trade:transactionID=1002",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "ibExecID": "EXEC-1002",
                "transactionID": "1002",
                "buySell": "BUY",
                "quantity": "10",
                "tradePrice": "101.00",
                "currency": "USD",
                "reportDate": "2026-02-14",
            },
        )
    ]

    with pytest.raises(MappingContractViolationError):
        mapping_build_canonical_batch(
            account_id="U_TEST",
            functional_currency="USD",
            raw_records=raw_records,
        )


def test_mapping_build_canonical_batch_skips_trade_rows_without_ib_exec_id() -> None:
    """Skip non-execution trade rows that do not include IB execution identity.

    Returns:
        None: Assertions validate skip behavior for non-execution trade rows.

    Raises:
        AssertionError: Raised when non-execution rows are treated as contract violations.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="Trades",
            source_row_ref="Trades:Trade:transactionID=37400900364",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "transactionID": "37400900364",
                "conid": "265598",
                "buySell": "BUY",
                "quantity": "10",
                "tradePrice": "101.00",
                "currency": "USD",
                "reportDate": "2026-02-14",
            },
        )
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert len(mapped_batch.instrument_upsert_requests) == 0
    assert len(mapped_batch.trade_fill_requests) == 0


def test_mapping_build_canonical_batch_skips_section_only_corp_action_rows() -> None:
    """Skip section-level corporate-action markers that do not represent action rows.

    Returns:
        None: Assertions validate skip behavior for section-only markers.

    Raises:
        AssertionError: Raised when section-only markers trigger contract violations.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="CorporateActions",
            source_row_ref="CorporateActions:section:1",
            report_date_local=date(2026, 2, 14),
            source_payload={},
        )
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert len(mapped_batch.instrument_upsert_requests) == 0
    assert len(mapped_batch.corp_action_requests) == 0


def test_mapping_build_canonical_batch_skips_non_execution_trades_rows() -> None:
    """Skip non-execution rows under Trades section without failing run.

    Returns:
        None: Assertions validate selective mapping behavior.

    Raises:
        AssertionError: Raised when non-execution rows are mapped as trade fills.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="Trades",
            source_row_ref="Trades:Order:idx=1",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "conid": "265598",
                "currency": "USD",
                "reportDate": "2026-02-14",
            },
        )
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert len(mapped_batch.trade_fill_requests) == 0


def test_mapping_build_canonical_batch_accepts_compact_report_date_format() -> None:
    """Accept compact reportDate format used by some IBKR payloads.

    Returns:
        None: Assertions validate deterministic date normalization.

    Raises:
        AssertionError: Raised when compact date cannot be parsed.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="Trades",
            source_row_ref="Trades:Trade:transactionID=1003",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "ibExecID": "EXEC-1003",
                "transactionID": "1003",
                "conid": "265598",
                "buySell": "BUY",
                "quantity": "1",
                "tradePrice": "101.00",
                "currency": "USD",
                "reportDate": "20260214",
                "dateTime": "2026-02-14T10:00:00+00:00",
            },
        )
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert len(mapped_batch.trade_fill_requests) == 1
    assert mapped_batch.trade_fill_requests[0].report_date_local == "2026-02-14"


def test_mapping_build_canonical_batch_accepts_slash_report_date_format() -> None:
    """Accept slash-separated reportDate values emitted by some legacy exports.

    Returns:
        None: Assertions validate deterministic date normalization.

    Raises:
        AssertionError: Raised when timestamped reportDate cannot be parsed.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="CashTransactions",
            source_row_ref="CashTransactions:CashTransaction:transactionID=2003",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "transactionID": "2003",
                "type": "DIV",
                "currency": "USD",
                "amount": "3.50",
                "reportDate": "2026/02/14",
            },
        )
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert len(mapped_batch.cashflow_requests) == 1
    assert mapped_batch.cashflow_requests[0].report_date_local == "2026-02-14"


def test_mapping_build_canonical_batch_fails_when_trade_timestamp_missing() -> None:
    """Fail fast when execution trade row omits dateTime timestamp.

    Returns:
        None: Assertions validate deterministic contract error behavior.

    Raises:
        AssertionError: Raised when missing trade timestamp is accepted.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="Trades",
            source_row_ref="Trades:Trade:transactionID=1004",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "ibExecID": "EXEC-1004",
                "transactionID": "1004",
                "conid": "265598",
                "buySell": "BUY",
                "quantity": "1",
                "tradePrice": "101.00",
                "currency": "USD",
                "reportDate": "2026-02-14",
            },
        )
    ]

    with pytest.raises(MappingContractViolationError, match="missing required field dateTime"):
        mapping_build_canonical_batch(
            account_id="U_TEST",
            functional_currency="USD",
            raw_records=raw_records,
        )


def test_mapping_build_canonical_batch_fails_when_required_trade_numeric_invalid() -> None:
    """Fail fast when required trade numeric field contains non-decimal text.

    Returns:
        None: Assertions validate deterministic numeric contract errors.

    Raises:
        AssertionError: Raised when invalid numeric value is accepted.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="Trades",
            source_row_ref="Trades:Trade:transactionID=1005",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "ibExecID": "EXEC-1005",
                "transactionID": "1005",
                "conid": "265598",
                "buySell": "BUY",
                "quantity": "N/A",
                "tradePrice": "101.00",
                "currency": "USD",
                "reportDate": "2026-02-14",
                "dateTime": "2026-02-14T10:00:00+00:00",
            },
        )
    ]

    with pytest.raises(MappingContractViolationError, match="invalid decimal field quantity"):
        mapping_build_canonical_batch(
            account_id="U_TEST",
            functional_currency="USD",
            raw_records=raw_records,
        )


def test_mapping_build_canonical_batch_fails_when_trade_timestamp_invalid() -> None:
    """Fail fast when trade timestamp uses unsupported datetime format.

    Returns:
        None: Assertions validate deterministic timestamp contract errors.

    Raises:
        AssertionError: Raised when malformed timestamp value is accepted.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="Trades",
            source_row_ref="Trades:Trade:transactionID=1006",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "ibExecID": "EXEC-1006",
                "transactionID": "1006",
                "conid": "265598",
                "buySell": "BUY",
                "quantity": "1",
                "tradePrice": "101.00",
                "currency": "USD",
                "reportDate": "2026-02-14",
                "dateTime": "14-02-2026 10:00:00",
            },
        )
    ]

    with pytest.raises(MappingContractViolationError, match="invalid timestamp field dateTime"):
        mapping_build_canonical_batch(
            account_id="U_TEST",
            functional_currency="USD",
            raw_records=raw_records,
        )


def test_mapping_build_canonical_batch_normalizes_trade_timestamp_to_utc() -> None:
    """Normalize trade timestamp with non-UTC offset into UTC ISO-8601.

    Returns:
        None: Assertions validate deterministic UTC timestamp normalization.

    Raises:
        AssertionError: Raised when timestamp is not normalized to UTC.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="Trades",
            source_row_ref="Trades:Trade:transactionID=1007",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "ibExecID": "EXEC-1007",
                "transactionID": "1007",
                "conid": "265598",
                "buySell": "BUY",
                "quantity": "1",
                "tradePrice": "101.00",
                "currency": "USD",
                "reportDate": "2026-02-14",
                "dateTime": "2026-02-14T12:00:00+02:00",
            },
        )
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert len(mapped_batch.trade_fill_requests) == 1
    assert mapped_batch.trade_fill_requests[0].trade_timestamp_utc == "2026-02-14T10:00:00+00:00"


def test_mapping_build_canonical_batch_fails_when_optional_cashflow_numeric_invalid() -> None:
    """Fail fast when optional cashflow numeric field is present but malformed.

    Returns:
        None: Assertions validate deterministic numeric contract errors.

    Raises:
        AssertionError: Raised when malformed optional numeric is accepted.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="CashTransactions",
            source_row_ref="CashTransactions:CashTransaction:transactionID=2004",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "transactionID": "2004",
                "type": "DIV",
                "currency": "USD",
                "amount": "3.50",
                "withholdingTax": "invalid",
                "reportDate": "2026-02-14",
            },
        )
    ]

    with pytest.raises(MappingContractViolationError, match="invalid decimal field withholdingTax"):
        mapping_build_canonical_batch(
            account_id="U_TEST",
            functional_currency="USD",
            raw_records=raw_records,
        )


def test_mapping_build_canonical_batch_fails_when_optional_cashflow_timestamp_invalid() -> None:
    """Fail fast when optional cashflow timestamp is present but malformed.

    Returns:
        None: Assertions validate deterministic timestamp contract errors.

    Raises:
        AssertionError: Raised when malformed optional timestamp is accepted.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="CashTransactions",
            source_row_ref="CashTransactions:CashTransaction:transactionID=2005",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "transactionID": "2005",
                "type": "DIV",
                "currency": "USD",
                "amount": "3.50",
                "reportDate": "2026-02-14",
                "dateTime": "2026/02/14 10:00:00",
            },
        )
    ]

    with pytest.raises(MappingContractViolationError, match="invalid timestamp field dateTime"):
        mapping_build_canonical_batch(
            account_id="U_TEST",
            functional_currency="USD",
            raw_records=raw_records,
        )


def test_mapping_build_canonical_batch_treats_optional_numeric_null_sentinel_as_none() -> None:
    """Map IBKR null sentinel numeric text to None for optional fields.

    Returns:
        None: Assertions validate deterministic sentinel normalization.

    Raises:
        AssertionError: Raised when null sentinel is treated as contract violation.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="CashTransactions",
            source_row_ref="CashTransactions:CashTransaction:transactionID=2006",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "transactionID": "2006",
                "type": "DIV",
                "currency": "USD",
                "amount": "3.50",
                "withholdingTax": "N/A",
                "reportDate": "2026-02-14",
            },
        )
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert len(mapped_batch.cashflow_requests) == 1
    assert mapped_batch.cashflow_requests[0].withholding_tax is None


def test_mapping_build_canonical_batch_treats_optional_timestamp_null_sentinel_as_none() -> None:
    """Map IBKR null sentinel timestamp text to None for optional fields.

    Returns:
        None: Assertions validate deterministic sentinel normalization.

    Raises:
        AssertionError: Raised when null sentinel is treated as invalid timestamp.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="CashTransactions",
            source_row_ref="CashTransactions:CashTransaction:transactionID=2007",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "transactionID": "2007",
                "type": "DIV",
                "currency": "USD",
                "amount": "3.50",
                "reportDate": "2026-02-14",
                "dateTime": "--",
            },
        )
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert len(mapped_batch.cashflow_requests) == 1
    assert mapped_batch.cashflow_requests[0].effective_at_utc is None


def test_mapping_build_canonical_batch_treats_report_date_null_sentinel_as_missing() -> None:
    """Fallback to row report date when payload reportDate contains null sentinel.

    Returns:
        None: Assertions validate deterministic report date fallback behavior.

    Raises:
        AssertionError: Raised when null sentinel reportDate triggers contract violation.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="CashTransactions",
            source_row_ref="CashTransactions:CashTransaction:transactionID=2008",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "transactionID": "2008",
                "type": "DIV",
                "currency": "USD",
                "amount": "3.50",
                "reportDate": "-",
            },
        )
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert len(mapped_batch.cashflow_requests) == 1
    assert mapped_batch.cashflow_requests[0].report_date_local == "2026-02-14"
