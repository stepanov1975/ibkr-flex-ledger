"""Raw payload extraction helpers for immutable raw row persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import xml.etree.ElementTree as element_tree


@dataclass(frozen=True)
class RawExtractedRow:
    """Typed representation of one extracted raw row.

    Attributes:
        section_name: Flex section name.
        source_row_ref: Deterministic source row identity within section.
        source_payload: Raw source row payload for JSON persistence.
    """

    section_name: str
    source_row_ref: str
    source_payload: dict[str, object]


@dataclass(frozen=True)
class RawPayloadExtractionResult:
    """Extraction result contract for one raw Flex payload.

    Attributes:
        report_date_local: Optional report date parsed from Flex statement metadata.
        rows: Extracted raw rows across all sections.
    """

    report_date_local: date | None
    rows: list[RawExtractedRow]


def job_raw_extract_payload_rows(payload_bytes: bytes) -> RawPayloadExtractionResult:
    """Extract raw section rows from Flex payload for immutable persistence.

    Args:
        payload_bytes: Raw immutable Flex payload bytes.

    Returns:
        RawPayloadExtractionResult: Parsed report date and extracted rows.

    Raises:
        ValueError: Raised when payload is empty or malformed XML.
    """

    if not payload_bytes:
        raise ValueError("payload_bytes must not be empty")

    try:
        root = element_tree.fromstring(payload_bytes)
    except element_tree.ParseError as error:
        raise ValueError("payload_bytes must contain valid XML") from error

    statements = root.findall(".//FlexStatement")
    if not statements:
        raise ValueError("FlexStatement node not found in payload")

    report_date_local = _job_raw_extract_report_date_local(statements[0])
    extracted_rows: list[RawExtractedRow] = []

    for statement in statements:
        for section_element in list(statement):
            section_name = section_element.tag.strip()
            if not section_name:
                continue

            section_rows = list(section_element)
            if not section_rows:
                extracted_rows.append(
                    RawExtractedRow(
                        section_name=section_name,
                        source_row_ref=f"{section_name}:section:1",
                        source_payload=dict(sorted(section_element.attrib.items())),
                    )
                )
                continue

            for row_index, row_element in enumerate(section_rows, start=1):
                row_payload = dict(sorted(row_element.attrib.items()))
                source_row_ref = _job_raw_build_source_row_ref(
                    section_name=section_name,
                    row_tag=row_element.tag,
                    row_payload=row_payload,
                    row_index=row_index,
                )
                extracted_rows.append(
                    RawExtractedRow(
                        section_name=section_name,
                        source_row_ref=source_row_ref,
                        source_payload=row_payload,
                    )
                )

    return RawPayloadExtractionResult(report_date_local=report_date_local, rows=extracted_rows)


def _job_raw_extract_report_date_local(statement: element_tree.Element) -> date | None:
    """Extract report date from statement metadata when available.

    Args:
        statement: `FlexStatement` element.

    Returns:
        date | None: Parsed report date or None when missing/invalid.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    candidate_values = [statement.attrib.get("reportDate"), statement.attrib.get("toDate")]
    for candidate_value in candidate_values:
        if not candidate_value:
            continue
        parsed_value = _job_raw_try_parse_local_date(candidate_value)
        if parsed_value is not None:
            return parsed_value
    return None


def _job_raw_try_parse_local_date(value: str) -> date | None:
    """Try parse known Flex local date formats.

    Args:
        value: Candidate date string.

    Returns:
        date | None: Parsed date when supported, else None.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    normalized_value = value.strip()
    if not normalized_value:
        return None

    supported_formats = ("%Y-%m-%d", "%Y%m%d")
    for date_format in supported_formats:
        try:
            return datetime.strptime(normalized_value, date_format).date()
        except ValueError:
            continue
    return None


def _job_raw_build_source_row_ref(
    section_name: str,
    row_tag: str,
    row_payload: dict[str, str],
    row_index: int,
) -> str:
    """Build deterministic source row reference for one section row.

    Args:
        section_name: Section container name.
        row_tag: Row element tag name.
        row_payload: Row attribute payload.
        row_index: One-based row index in section.

    Returns:
        str: Deterministic source row reference.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    preferred_keys = (
        "transactionID",
        "transactionId",
        "tradeID",
        "tradeId",
        "actionID",
        "actionId",
        "ibExecID",
        "ibExecId",
        "execID",
        "execId",
        "id",
    )

    for preferred_key in preferred_keys:
        preferred_value = row_payload.get(preferred_key, "").strip()
        if preferred_value:
            return f"{section_name}:{row_tag}:{preferred_key}={preferred_value}"

    return f"{section_name}:{row_tag}:idx={row_index}"
