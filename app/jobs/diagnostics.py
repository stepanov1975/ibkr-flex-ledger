"""Shared diagnostics parsing helpers for ingestion workflows."""

from __future__ import annotations

from .section_preflight import MISSING_REQUIRED_SECTION_CODE


def job_extract_missing_sections_from_diagnostics(diagnostics) -> dict[str, list[str]]:
    """Extract missing-section lists from run diagnostics timeline.

    Args:
        diagnostics: Run diagnostics payload.

    Returns:
        dict[str, list[str]]: Missing section lists by category.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    missing_sections: list[str] = []
    missing_hard_required: list[str] = []
    missing_reconciliation_required: list[str] = []

    if not isinstance(diagnostics, list):
        return {
            "missing_sections": missing_sections,
            "missing_hard_required": missing_hard_required,
            "missing_reconciliation_required": missing_reconciliation_required,
        }

    for event in diagnostics:
        if not isinstance(event, dict):
            continue
        if event.get("error_code") != MISSING_REQUIRED_SECTION_CODE:
            continue
        missing_sections = [str(value) for value in event.get("missing_sections", [])]
        missing_hard_required = [str(value) for value in event.get("missing_hard_required", [])]
        missing_reconciliation_required = [str(value) for value in event.get("missing_reconciliation_required", [])]
        break

    return {
        "missing_sections": missing_sections,
        "missing_hard_required": missing_hard_required,
        "missing_reconciliation_required": missing_reconciliation_required,
    }
