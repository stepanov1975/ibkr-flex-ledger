"""Regression tests for raw payload extraction helpers."""

from datetime import date

from app.jobs.raw_extraction import job_raw_extract_payload_rows


def test_jobs_raw_extraction_extracts_section_names_and_source_refs() -> None:
    """Extract deterministic source row references and section payload rows.

    Returns:
        None: Assertions validate extracted row contract.

    Raises:
        AssertionError: Raised when extraction output is malformed.
    """

    payload_bytes = (
        b"<FlexQueryResponse><FlexStatements count=\"1\">"
        b"<FlexStatement accountId=\"U_TEST\" toDate=\"20260214\">"
        b"<Trades><Trade transactionID=\"TX100\" quantity=\"10\" /></Trades>"
        b"<CashTransactions><CashTransaction amount=\"12.34\" /></CashTransactions>"
        b"<OpenPositions /></FlexStatement></FlexStatements></FlexQueryResponse>"
    )

    extraction_result = job_raw_extract_payload_rows(payload_bytes=payload_bytes)

    assert extraction_result.report_date_local == date(2026, 2, 14)
    assert len(extraction_result.rows) == 3
    assert extraction_result.rows[0].section_name == "Trades"
    assert extraction_result.rows[0].source_row_ref == "Trades:Trade:transactionID=TX100"
    assert extraction_result.rows[1].section_name == "CashTransactions"
    assert extraction_result.rows[1].source_row_ref == "CashTransactions:CashTransaction:idx=1"
    assert extraction_result.rows[2].section_name == "OpenPositions"
    assert extraction_result.rows[2].source_row_ref == "OpenPositions:section:1"


def test_jobs_raw_extraction_extracts_nested_section_rows() -> None:
    """Extract leaf data rows when a section contains nested container elements.

    Returns:
        None: Assertions validate nested row extraction behavior.

    Raises:
        AssertionError: Raised when nested leaf rows are not extracted.
    """

    payload_bytes = (
        b"<FlexQueryResponse><FlexStatements count=\"1\">"
        b"<FlexStatement accountId=\"U_TEST\" toDate=\"20260214\">"
        b"<FxPositions><FxLots><FxLot functionalCurrency=\"USD\" quantity=\"100\" />"
        b"<FxLot functionalCurrency=\"EUR\" quantity=\"50\" /></FxLots></FxPositions>"
        b"</FlexStatement></FlexStatements></FlexQueryResponse>"
    )

    extraction_result = job_raw_extract_payload_rows(payload_bytes=payload_bytes)

    assert len(extraction_result.rows) == 2
    assert extraction_result.rows[0].section_name == "FxPositions"
    assert extraction_result.rows[0].source_payload == {"functionalCurrency": "USD", "quantity": "100"}
    assert extraction_result.rows[0].source_row_ref == "FxPositions:FxLot:idx=1"
    assert extraction_result.rows[1].section_name == "FxPositions"
    assert extraction_result.rows[1].source_payload == {"functionalCurrency": "EUR", "quantity": "50"}
    assert extraction_result.rows[1].source_row_ref == "FxPositions:FxLot:idx=2"
