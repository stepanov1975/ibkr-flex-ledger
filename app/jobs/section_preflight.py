"""Required Flex section preflight validation for ingestion workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .flex_payload_validation import job_flex_parse_payload_with_statements

MISSING_REQUIRED_SECTION_CODE: Final[str] = "MISSING_REQUIRED_SECTION"

HARD_REQUIRED_FLEX_SECTIONS: Final[tuple[str, ...]] = (
    "Trades",
    "OpenPositions",
    "CashTransactions",
    "CorporateActions",
    "ConversionRates",
    "SecuritiesInfo",
    "AccountInformation",
)

RECONCILIATION_REQUIRED_FLEX_SECTIONS: Final[tuple[str, ...]] = (
    "MTMPerformanceSummaryInBase",
    "FIFOPerformanceSummaryInBase",
)

FUTURE_PROOF_FLEX_SECTIONS: Final[tuple[str, ...]] = (
    "InterestAccruals",
    "ChangeInDividendAccruals",
    "OpenDividendAccruals",
    "ChangeInNAV",
    "StmtFunds",
    "UnbundledCommissionDetails",
)


class MissingRequiredSectionError(ValueError):
    """Raised when hard-required or reconciliation-required sections are missing."""


@dataclass(frozen=True)
class SectionPreflightResult:
    """Result payload for required section preflight checks.

    Attributes:
        detected_sections: Sorted detected section names from payload.
        missing_hard_required: Sorted missing hard-required section names.
        missing_reconciliation_required: Sorted missing reconciliation-required section names.
    """

    detected_sections: tuple[str, ...]
    missing_hard_required: tuple[str, ...]
    missing_reconciliation_required: tuple[str, ...]

    def section_preflight_is_valid(self) -> bool:
        """Return whether section preflight passed.

        Returns:
            bool: True when no required sections are missing.

        Raises:
            RuntimeError: This method does not raise runtime errors.
        """

        return not self.missing_hard_required and not self.missing_reconciliation_required


# FSN[2026-02-14]: ALWAYS treat diagnostics payload as JSON array of objects.
# Context: Task 3 requires structured timeline/error payload in ingestion_run.diagnostics.
# Guard: builder returns list[dict] and validator enforces non-empty missing sections.
# Test: test_jobs_section_preflight_reports_missing_required_sections
def job_section_preflight_build_missing_required_diagnostics(
    preflight_result: SectionPreflightResult,
) -> list[dict[str, object]]:
    """Build deterministic diagnostics array for required-section failures.

    Args:
        preflight_result: Output from section preflight validation.

    Returns:
        list[dict[str, object]]: JSON-array-compatible diagnostics payload.

    Raises:
        ValueError: Raised when called without missing required sections.
    """

    if preflight_result.section_preflight_is_valid():
        raise ValueError("preflight_result must include missing required sections")

    missing_sections = sorted(
        set(preflight_result.missing_hard_required).union(preflight_result.missing_reconciliation_required)
    )
    return [
        {
            "stage": "preflight",
            "status": "failed",
            "error_code": MISSING_REQUIRED_SECTION_CODE,
            "missing_sections": missing_sections,
            "missing_hard_required": list(preflight_result.missing_hard_required),
            "missing_reconciliation_required": list(preflight_result.missing_reconciliation_required),
            "detected_sections": list(preflight_result.detected_sections),
        }
    ]


def job_section_preflight_validate_required_sections(
    payload_bytes: bytes,
    reconciliation_enabled: bool,
) -> SectionPreflightResult:
    """Validate payload section set against frozen required section matrix.

    Args:
        payload_bytes: Raw immutable Flex payload bytes.
        reconciliation_enabled: Whether reconciliation-required section checks are enforced.

    Returns:
        SectionPreflightResult: Deterministic section validation result.

    Raises:
        ValueError: Raised when payload bytes are empty or malformed.
    """

    detected_sections = job_section_preflight_extract_section_names(payload_bytes=payload_bytes)

    missing_hard_required = tuple(sorted(set(HARD_REQUIRED_FLEX_SECTIONS) - detected_sections))
    missing_reconciliation_required = tuple()
    if reconciliation_enabled:
        missing_reconciliation_required = tuple(sorted(set(RECONCILIATION_REQUIRED_FLEX_SECTIONS) - detected_sections))

    return SectionPreflightResult(
        detected_sections=tuple(sorted(detected_sections)),
        missing_hard_required=missing_hard_required,
        missing_reconciliation_required=missing_reconciliation_required,
    )


def job_section_preflight_extract_section_names(payload_bytes: bytes) -> set[str]:
    """Extract section container names from a Flex XML payload.

    Args:
        payload_bytes: Raw immutable Flex payload bytes.

    Returns:
        set[str]: Section names detected under `FlexStatement` elements.

    Raises:
        ValueError: Raised when payload is empty or does not contain `FlexStatement`.
    """

    _, statements = job_flex_parse_payload_with_statements(payload_bytes=payload_bytes)

    section_names: set[str] = set()
    for statement in statements:
        for section_element in list(statement):
            section_name = section_element.tag.strip()
            if section_name:
                section_names.add(section_name)

    return section_names


def job_section_preflight_raise_for_missing_required(preflight_result: SectionPreflightResult) -> None:
    """Raise deterministic error when required sections are missing.

    Args:
        preflight_result: Section preflight validation result.

    Returns:
        None: This function does not return a value.

    Raises:
        MissingRequiredSectionError: Raised when one or more required sections are missing.
    """

    if preflight_result.section_preflight_is_valid():
        return

    missing_sections = sorted(
        set(preflight_result.missing_hard_required).union(preflight_result.missing_reconciliation_required)
    )
    message = f"{MISSING_REQUIRED_SECTION_CODE}: missing sections={', '.join(missing_sections)}"
    raise MissingRequiredSectionError(message)
