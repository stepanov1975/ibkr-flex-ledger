"""Validate broker account and base-currency context in Flex reports."""

from __future__ import annotations

from typing import Final

from .flex_payload_validation import job_flex_parse_payload_with_statements


REPORT_CONTEXT_INVALID_CODE: Final[str] = "REPORT_CONTEXT_INVALID"


class ReportContextError(ValueError):
    """Raised when a Flex report does not identify one supported account."""

    code = REPORT_CONTEXT_INVALID_CODE

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


def job_validate_report_context(
    payload_bytes: bytes,
    expected_broker_account_ids: frozenset[str] = frozenset(),
) -> str:
    """Return the broker account ID after validating report context."""

    try:
        _, statements = job_flex_parse_payload_with_statements(payload_bytes=payload_bytes)
    except ValueError as error:
        raise ReportContextError(str(error)) from error

    if len(statements) != 1:
        raise ReportContextError("report must contain exactly one FlexStatement")

    statement = statements[0]
    account_information = statement.findall(".//AccountInformation")
    if len(account_information) != 1:
        raise ReportContextError("report must contain exactly one AccountInformation element")
    account_information_element = account_information[0]

    account_currency = account_information_element.attrib.get("currency", "").strip().upper()
    if account_currency != "USD":
        raise ReportContextError("AccountInformation currency must be USD")

    if "accountId" not in statement.attrib and "accountId" not in account_information_element.attrib:
        raise ReportContextError("broker account ID must appear in FlexStatement or AccountInformation")

    broker_account_ids: set[str] = set()
    for element in statement.iter():
        if "accountId" not in element.attrib:
            continue
        broker_account_id = element.attrib["accountId"].strip()
        if not broker_account_id:
            raise ReportContextError("accountId values must not be blank")
        broker_account_ids.add(broker_account_id)

    if len(broker_account_ids) != 1:
        raise ReportContextError("report contains inconsistent broker account IDs")

    broker_account_id = next(iter(broker_account_ids))
    normalized_expected_ids = {value.strip() for value in expected_broker_account_ids}
    if "" in normalized_expected_ids:
        raise ReportContextError("expected broker account IDs must not be blank")
    if normalized_expected_ids and normalized_expected_ids != {broker_account_id}:
        raise ReportContextError("report broker account ID does not match successful history")

    return broker_account_id


__all__ = [
    "REPORT_CONTEXT_INVALID_CODE",
    "ReportContextError",
    "job_validate_report_context",
]
