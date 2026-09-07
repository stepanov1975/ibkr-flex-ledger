"""Regression tests for Task 5 canonical mapping pipeline behavior."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from app.mapping.service import (
    MappingContractViolationError,
    RawRecordForMapping,
    mapping_build_canonical_batch,
)


def _execution_trade(**overrides: object) -> RawRecordForMapping:
    payload: dict[str, object] = {
        "levelOfDetail": "EXECUTION",
        "transactionID": "37400900364",
        "tradeID": "9921",
        "conid": "265598",
        "buySell": "BUY",
        "quantity": "10",
        "tradePrice": "101.00",
        "currency": "USD",
        "reportDate": "2026-02-14",
        "dateTime": "2026-02-14T10:00:00+00:00",
    }
    payload.update(overrides)
    return RawRecordForMapping(
        raw_record_id=uuid4(),
        ingestion_run_id=uuid4(),
        section_name="Trades",
        source_row_ref="Trades:Trade:transactionID=37400900364",
        report_date_local=date(2026, 2, 14),
        source_payload=payload,
    )


def _open_position(**overrides: object) -> RawRecordForMapping:
    payload: dict[str, object] = {
        "conid": "815232555",
        "symbol": "ALEX  260821P00010000",
        "assetCategory": "OPT",
        "currency": "USD",
        "position": "-2",
        "markPrice": "0.05",
        "costBasisMoney": "-120",
        "fifoPnlUnrealized": "110",
        "fxRateToBase": "1",
        "multiplier": "100",
        "reportDate": "20260820",
    }
    payload.update(overrides)
    return RawRecordForMapping(
        raw_record_id=uuid4(),
        ingestion_run_id=uuid4(),
        section_name="OpenPositions",
        source_row_ref="OpenPositions:OpenPosition:idx=1",
        report_date_local=date(2026, 8, 20),
        source_payload=payload,
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


def test_mapping_ignores_empty_section_placeholders() -> None:
    """Treat present-but-empty optional event sections as zero canonical rows."""

    ingestion_run_id = uuid4()
    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=ingestion_run_id,
            section_name=section_name,
            source_row_ref=f"{section_name}:section:1",
            report_date_local=date(2026, 2, 14),
            source_payload={},
        )
        for section_name in ("Trades", "CashTransactions", "ConversionRates", "CorporateActions")
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert mapped_batch.instrument_upsert_requests == ()
    assert mapped_batch.trade_fill_requests == ()
    assert mapped_batch.cashflow_requests == ()
    assert mapped_batch.fx_requests == ()
    assert mapped_batch.corp_action_requests == ()


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


def test_mapping_uses_transaction_id_for_execution_without_ib_exec_id() -> None:
    batch = mapping_build_canonical_batch("U_TEST", "USD", [_execution_trade()])

    assert batch.trade_fill_requests[0].ib_exec_id == "FLEX_TXN:37400900364"


def test_mapping_uses_trade_id_when_execution_transaction_id_is_blank() -> None:
    batch = mapping_build_canonical_batch(
        "U_TEST", "USD", [_execution_trade(transactionID="")]
    )

    assert batch.trade_fill_requests[0].ib_exec_id == "FLEX_TRADE:9921"


def test_mapping_preserves_nonblank_ib_exec_id_exactly_as_supplied() -> None:
    batch = mapping_build_canonical_batch(
        "U_TEST", "USD", [_execution_trade(ibExecID=" EXEC-9921 ")]
    )

    assert batch.trade_fill_requests[0].ib_exec_id == " EXEC-9921 "


def test_mapping_rejects_execution_without_any_stable_identity() -> None:
    with pytest.raises(MappingContractViolationError, match="stable execution identity"):
        mapping_build_canonical_batch(
            "U_TEST", "USD", [_execution_trade(transactionID="", tradeID="")]
        )


@pytest.mark.parametrize("row_tag", ["Order", "Lot", "SymbolSummary"])
def test_mapping_excludes_non_trade_rows_even_with_execution_fields(row_tag: str) -> None:
    row = replace(
        _execution_trade(),
        source_row_ref=f"Trades:{row_tag}:transactionID=37400900364",
    )

    batch = mapping_build_canonical_batch("U_TEST", "USD", [row])

    assert batch.trade_fill_requests == ()


def test_mapping_skips_non_execution_trade_without_ib_exec_id() -> None:
    batch = mapping_build_canonical_batch(
        "U_TEST", "USD", [_execution_trade(levelOfDetail="", transactionID="")]
    )

    assert batch.instrument_upsert_requests == ()
    assert batch.trade_fill_requests == ()


def test_mapping_open_position_upserts_option_instrument() -> None:
    batch = mapping_build_canonical_batch("U_TEST", "USD", [_open_position()])

    assert len(batch.instrument_upsert_requests) == 1
    assert batch.instrument_upsert_requests[0].conid == "815232555"
    assert batch.instrument_upsert_requests[0].asset_category == "OPT"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("position", ""),
        ("position", "invalid"),
        ("markPrice", "invalid"),
        ("costBasisMoney", "invalid"),
        ("fifoPnlUnrealized", "invalid"),
        ("fxRateToBase", "invalid"),
        ("multiplier", "invalid"),
    ],
)
def test_mapping_open_position_rejects_invalid_numeric_contract(
    field: str,
    value: str,
) -> None:
    with pytest.raises(MappingContractViolationError):
        mapping_build_canonical_batch(
            "U_TEST", "USD", [_open_position(**{field: value})]
        )


def test_mapping_open_position_allows_blank_optional_values() -> None:
    batch = mapping_build_canonical_batch(
        "U_TEST",
        "USD",
        [
            _open_position(
                markPrice="",
                costBasisMoney="",
                fifoPnlUnrealized="",
                fxRateToBase="",
                multiplier="",
            )
        ],
    )

    assert len(batch.instrument_upsert_requests) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fxRateToBase", "0"),
        ("fxRateToBase", "-1"),
        ("multiplier", "0"),
        ("multiplier", "-100"),
    ],
)
def test_mapping_open_position_rejects_non_positive_rate_or_multiplier(
    field: str,
    value: str,
) -> None:
    with pytest.raises(MappingContractViolationError, match=f"{field} must be positive"):
        mapping_build_canonical_batch(
            "U_TEST", "USD", [_open_position(**{field: value})]
        )


@pytest.mark.parametrize("multiplier", [None, "", "N/A", "0", "-100"])
def test_mapping_option_execution_requires_positive_multiplier(
    multiplier: str | None,
) -> None:
    overrides: dict[str, object] = {"assetCategory": "OPT"}
    if multiplier is not None:
        overrides["multiplier"] = multiplier

    with pytest.raises(MappingContractViolationError, match="multiplier must be positive"):
        mapping_build_canonical_batch(
            "U_TEST", "USD", [_execution_trade(**overrides)]
        )


@pytest.mark.parametrize("fx_rate", ["0", "-1"])
def test_mapping_trade_rejects_non_positive_fx_rate(fx_rate: str) -> None:
    with pytest.raises(MappingContractViolationError, match="fxRateToBase must be positive"):
        mapping_build_canonical_batch(
            "U_TEST", "USD", [_execution_trade(fxRateToBase=fx_rate)]
        )


def test_mapping_option_execution_accepts_normalized_positive_multiplier() -> None:
    batch = mapping_build_canonical_batch(
        "U_TEST",
        "USD",
        [_execution_trade(assetCategory="OPT", multiplier=" 1,000 ")],
    )

    assert len(batch.trade_fill_requests) == 1


@pytest.mark.parametrize("asset_category", ["CASH", "FX"])
def test_mapping_excludes_cash_and_fx_open_positions(asset_category: str) -> None:
    batch = mapping_build_canonical_batch(
        "U_TEST", "USD", [_open_position(assetCategory=asset_category)]
    )

    assert batch.instrument_upsert_requests == ()


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


def test_mapping_build_canonical_batch_ignores_trade_token_outside_row_tag() -> None:
    """Ignore rows whose row-tag is not `Trade` even when reference text contains `:Trade:`.

    Returns:
        None: Assertions validate row-tag based routing behavior.

    Raises:
        AssertionError: Raised when non-Trade row tags are routed as trades.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="Trades",
            source_row_ref="Trades:Order:id=ROW:Trade:marker",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "ibExecID": "EXEC-ORDER-1",
                "transactionID": "200100",
                "conid": "265598",
                "buySell": "BUY",
                "quantity": "1",
                "tradePrice": "10.00",
                "currency": "USD",
                "reportDate": "2026-02-14",
                "dateTime": "2026-02-14T10:00:00+00:00",
            },
        )
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert len(mapped_batch.trade_fill_requests) == 0


def test_mapping_build_canonical_batch_ignores_corp_action_token_outside_row_tag() -> None:
    """Ignore rows whose row-tag is not `CorporateAction` despite reference token text.

    Returns:
        None: Assertions validate row-tag based corp-action routing.

    Raises:
        AssertionError: Raised when non-CorporateAction row tags are routed as corp actions.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="CorporateActions",
            source_row_ref="CorporateActions:Summary:id=ROW:CorporateAction:marker",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "actionID": "CA-2001",
                "transactionID": "4001",
                "conid": "265598",
                "type": "IC",
                "reportDate": "2026-02-14",
            },
        )
    ]

    mapped_batch = mapping_build_canonical_batch(
        account_id="U_TEST",
        functional_currency="USD",
        raw_records=raw_records,
    )

    assert len(mapped_batch.corp_action_requests) == 0


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


def test_mapping_build_canonical_batch_accepts_required_trade_decimal_with_thousands_separator() -> None:
    """Accept comma-separated thousands formatting for required trade numerics.

    Returns:
        None: Assertions validate deterministic decimal normalization.

    Raises:
        AssertionError: Raised when comma-formatted required numerics are rejected.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="Trades",
            source_row_ref="Trades:Trade:transactionID=1005A",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "ibExecID": "EXEC-1005A",
                "transactionID": "1005A",
                "conid": "265598",
                "buySell": "BUY",
                "quantity": "1,234.56",
                "tradePrice": "101.00",
                "currency": "USD",
                "reportDate": "2026-02-14",
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
    assert mapped_batch.trade_fill_requests[0].quantity == "1234.56"


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


def test_mapping_build_canonical_batch_accepts_optional_cashflow_decimal_with_thousands_separator() -> None:
    """Accept comma-separated thousands formatting for optional cashflow numerics.

    Returns:
        None: Assertions validate deterministic decimal normalization.

    Raises:
        AssertionError: Raised when comma-formatted optional numerics are rejected.
    """

    raw_records = [
        RawRecordForMapping(
            raw_record_id=uuid4(),
            ingestion_run_id=uuid4(),
            section_name="CashTransactions",
            source_row_ref="CashTransactions:CashTransaction:transactionID=2004A",
            report_date_local=date(2026, 2, 14),
            source_payload={
                "transactionID": "2004A",
                "type": "DIV",
                "currency": "USD",
                "amount": "3.50",
                "withholdingTax": "1,000.25",
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
    assert mapped_batch.cashflow_requests[0].withholding_tax == "1000.25"


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


def test_conversion_rate_identity_uses_own_date_and_pair_independent_of_row_ordinal() -> None:
    first = RawRecordForMapping(
        raw_record_id=uuid4(),
        ingestion_run_id=uuid4(),
        section_name="ConversionRates",
        source_row_ref="ConversionRates:ConversionRate:idx=1",
        report_date_local=date(2026, 8, 21),
        source_payload={"fromCurrency": "EUR", "toCurrency": "USD", "reportDate": "20260820", "rate": "1.1"},
    )
    moved = replace(first, source_row_ref="ConversionRates:ConversionRate:idx=9")
    tomorrow = replace(first, source_payload={**first.source_payload, "reportDate": "20260821"})
    another_pair = replace(first, source_payload={**first.source_payload, "toCurrency": "GBP"})
    broker_id = replace(first, source_payload={**first.source_payload, "transactionID": "BROKER-FX-1"})
    requests = [
        mapping_build_canonical_batch("A", "USD", [row]).fx_requests[0]
        for row in (first, moved, tomorrow, another_pair, broker_id)
    ]
    assert requests[0].transaction_id == requests[1].transaction_id
    assert requests[0].report_date_local == "2026-08-20"
    assert len({request.transaction_id for request in (requests[0], *requests[2:])}) == 4
    assert requests[-1].transaction_id == "BROKER-FX-1"
