"""Report account and base-currency validation regressions."""

from __future__ import annotations

import pytest

from app.jobs.report_context import (
    REPORT_CONTEXT_INVALID_CODE,
    ReportContextError,
    job_validate_report_context,
)


def test_report_context_accepts_one_usd_broker_account() -> None:
    payload = b"""<FlexQueryResponse><FlexStatements count="1">
    <FlexStatement accountId="U123">
      <AccountInformation accountId="U123" currency="USD" />
      <Trades><Trade accountId="U123" transactionID="TX-1" /></Trades>
    </FlexStatement></FlexStatements></FlexQueryResponse>"""

    assert job_validate_report_context(payload) == "U123"


@pytest.mark.parametrize(
    "payload",
    [
        b"""<FlexQueryResponse><FlexStatements count="1">
        <FlexStatement accountId="U123">
          <AccountInformation currency="USD" />
        </FlexStatement></FlexStatements></FlexQueryResponse>""",
        b"""<FlexQueryResponse><FlexStatements count="1">
        <FlexStatement>
          <AccountInformation accountId="U123" currency="USD" />
        </FlexStatement></FlexStatements></FlexQueryResponse>""",
    ],
)
def test_report_context_resolves_broker_id_from_header_or_account_information(payload: bytes) -> None:
    assert job_validate_report_context(payload) == "U123"


def test_report_context_rejects_linked_account_statements() -> None:
    payload = b"""<FlexQueryResponse><FlexStatements count="2">
    <FlexStatement accountId="U123">
      <AccountInformation accountId="U123" currency="USD" />
    </FlexStatement>
    <FlexStatement accountId="U456">
      <AccountInformation accountId="U456" currency="USD" />
    </FlexStatement>
    </FlexStatements></FlexQueryResponse>"""

    with pytest.raises(ReportContextError) as caught:
        job_validate_report_context(payload)

    assert caught.value.code == REPORT_CONTEXT_INVALID_CODE


def test_report_context_rejects_multiple_account_information_elements() -> None:
    payload = b"""<FlexQueryResponse><FlexStatements count="1">
    <FlexStatement accountId="U123">
      <AccountInformation accountId="U123" currency="USD" />
      <AccountInformation accountId="U123" currency="USD" />
    </FlexStatement></FlexStatements></FlexQueryResponse>"""

    with pytest.raises(ReportContextError):
        job_validate_report_context(payload)


def test_report_context_rejects_broker_id_found_only_on_data_row() -> None:
    payload = b"""<FlexQueryResponse><FlexStatements count="1">
    <FlexStatement>
      <AccountInformation currency="USD" />
      <Trades><Trade accountId="U123" /></Trades>
    </FlexStatement></FlexStatements></FlexQueryResponse>"""

    with pytest.raises(ReportContextError):
        job_validate_report_context(payload)


@pytest.mark.parametrize(
    "conflicting_element",
    [
        '<AccountInformation accountId="U456" currency="USD" />',
        '<AccountInformation accountId="U123" currency="USD" />'
        '<Trades><Trade accountId="U456" /></Trades>',
    ],
)
def test_report_context_rejects_conflicting_header_or_row_ids(conflicting_element: str) -> None:
    payload = (
        '<FlexQueryResponse><FlexStatements count="1">'
        '<FlexStatement accountId="U123">'
        f"{conflicting_element}"
        "</FlexStatement></FlexStatements></FlexQueryResponse>"
    ).encode()

    with pytest.raises(ReportContextError):
        job_validate_report_context(payload)


@pytest.mark.parametrize(
    "payload",
    [
        b"""<FlexQueryResponse><FlexStatements count="1">
        <FlexStatement accountId="U123" />
        </FlexStatements></FlexQueryResponse>""",
        b"""<FlexQueryResponse><FlexStatements count="1">
        <FlexStatement><AccountInformation currency="USD" /></FlexStatement>
        </FlexStatements></FlexQueryResponse>""",
        b"""<FlexQueryResponse><FlexStatements count="1">
        <FlexStatement accountId=" ">
          <AccountInformation accountId="U123" currency="USD" />
        </FlexStatement></FlexStatements></FlexQueryResponse>""",
        b"""<FlexQueryResponse><FlexStatements count="1">
        <FlexStatement accountId="U123">
          <AccountInformation currency="USD" />
          <Trades><Trade accountId=" " /></Trades>
        </FlexStatement></FlexStatements></FlexQueryResponse>""",
    ],
)
def test_report_context_rejects_missing_or_blank_account_metadata(payload: bytes) -> None:
    with pytest.raises(ReportContextError) as caught:
        job_validate_report_context(payload)

    assert caught.value.code == "REPORT_CONTEXT_INVALID"


@pytest.mark.parametrize("currency", ["", "EUR"])
def test_report_context_rejects_missing_or_non_usd_base_currency(currency: str) -> None:
    payload = (
        '<FlexQueryResponse><FlexStatements count="1">'
        '<FlexStatement accountId="U123">'
        f'<AccountInformation accountId="U123" currency="{currency}" />'
        "</FlexStatement></FlexStatements></FlexQueryResponse>"
    ).encode()

    with pytest.raises(ReportContextError):
        job_validate_report_context(payload)


def test_report_context_accepts_prior_successful_broker_account_match() -> None:
    payload = b"""<FlexQueryResponse><FlexStatements count="1">
    <FlexStatement accountId="U123">
      <AccountInformation accountId="U123" currency="USD" />
    </FlexStatement></FlexStatements></FlexQueryResponse>"""

    assert job_validate_report_context(payload, frozenset({"U123"})) == "U123"


def test_report_context_rejects_broker_account_change_after_success() -> None:
    payload = b"""<FlexQueryResponse><FlexStatements count="1">
    <FlexStatement accountId="U456">
      <AccountInformation accountId="U456" currency="USD" />
    </FlexStatement></FlexStatements></FlexQueryResponse>"""

    with pytest.raises(ReportContextError) as caught:
        job_validate_report_context(payload, frozenset({"U123"}))

    assert caught.value.code == "REPORT_CONTEXT_INVALID"
