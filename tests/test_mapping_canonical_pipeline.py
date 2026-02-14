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
