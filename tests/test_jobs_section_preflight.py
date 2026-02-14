"""Regression tests for section preflight required-section validation."""

from app.jobs import (
    MISSING_REQUIRED_SECTION_CODE,
    job_section_preflight_build_missing_required_diagnostics,
    job_section_preflight_validate_required_sections,
)


def test_jobs_section_preflight_reports_missing_required_sections() -> None:
    """Report deterministic missing section diagnostics for incomplete payload.

    Returns:
        None: Assertions validate behavior.

    Raises:
        AssertionError: Raised when validation output is unexpected.
    """

    payload = b"""<FlexQueryResponse><FlexStatements count=\"1\"><FlexStatement><Trades /></FlexStatement></FlexStatements></FlexQueryResponse>"""

    preflight_result = job_section_preflight_validate_required_sections(
        payload_bytes=payload,
        reconciliation_enabled=False,
    )

    assert not preflight_result.section_preflight_is_valid()
    assert "OpenPositions" in preflight_result.missing_hard_required

    diagnostics = job_section_preflight_build_missing_required_diagnostics(preflight_result)

    assert isinstance(diagnostics, list)
    assert diagnostics[0]["error_code"] == MISSING_REQUIRED_SECTION_CODE
    assert "OpenPositions" in diagnostics[0]["missing_sections"]
