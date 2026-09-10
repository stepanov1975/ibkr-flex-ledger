"""Raw payload extraction helpers for immutable raw row persistence."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from hashlib import sha256
import json
import xml.etree.ElementTree as element_tree

from app.domain.flex_parsing import domain_flex_parse_local_date

from .flex_payload_validation import job_flex_parse_payload_with_statements


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

    _, statements = job_flex_parse_payload_with_statements(payload_bytes=payload_bytes)

    report_date_local = _job_raw_extract_report_date_local(statements[0])
    extracted_rows: list[RawExtractedRow] = []

    for statement in statements:
        for section_element in list(statement):
            section_name = section_element.tag.strip()
            if not section_name:
                continue

            leaf_rows = _job_raw_collect_section_leaf_rows(section_element)
            if not leaf_rows:
                extracted_rows.append(
                    RawExtractedRow(
                        section_name=section_name,
                        source_row_ref=f"{section_name}:section:1",
                        source_payload=dict(sorted(section_element.attrib.items())),
                    )
                )
                continue

            for row_index, leaf_row in enumerate(leaf_rows, start=1):
                row_element = leaf_row.row_element
                row_payload = leaf_row.row_payload
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
                        source_payload={key: value for key, value in row_payload.items()},
                    )
                )

    _job_raw_disambiguate_source_row_refs(extracted_rows)
    return RawPayloadExtractionResult(report_date_local=report_date_local, rows=extracted_rows)


def _job_raw_disambiguate_source_row_refs(rows: list[RawExtractedRow]) -> None:
    """Keep every colliding row while retaining existing refs for unique rows."""

    reference_counts = Counter((row.section_name, row.source_row_ref) for row in rows)
    reserved_refs = set(reference_counts)
    collisions = sorted(
        (row.section_name, row.source_row_ref, json.dumps(row.source_payload, sort_keys=True), index)
        for index, row in enumerate(rows)
        if reference_counts[(row.section_name, row.source_row_ref)] > 1
    )
    occurrences: Counter[tuple[str, str]] = Counter()
    for section_name, source_row_ref, payload_json, index in collisions:
        payload_digest = sha256(payload_json.encode()).hexdigest()
        reference_prefix = f"{source_row_ref}:payload={payload_digest}"
        occurrence_key = (section_name, reference_prefix)
        # Reserve original refs too: broker IDs can contain our suffix syntax.
        while True:
            occurrences[occurrence_key] += 1
            disambiguated_ref = f"{reference_prefix}:occurrence={occurrences[occurrence_key]}"
            if (section_name, disambiguated_ref) not in reserved_refs:
                break
        reserved_refs.add((section_name, disambiguated_ref))
        rows[index] = replace(rows[index], source_row_ref=disambiguated_ref)


@dataclass(frozen=True)
class RawSectionLeafRow:
    """Leaf row element with merged ancestor context attributes.

    Attributes:
        row_element: Extracted leaf XML element.
        row_payload: Merged payload where child attributes override ancestor keys.
    """

    row_element: element_tree.Element
    row_payload: dict[str, str]


def _job_raw_collect_section_leaf_rows(
    section_element: element_tree.Element,
    parent_attributes: dict[str, str] | None = None,
) -> list[RawSectionLeafRow]:
    """Collect leaf row elements recursively under one Flex section.

    Args:
        section_element: Section container element under `FlexStatement`.
        parent_attributes: Optional inherited attributes from ancestor containers.

    Returns:
        list[RawSectionLeafRow]: Deterministically ordered leaf rows with merged payload context.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    inherited_attributes = dict(parent_attributes or {})
    inherited_attributes.update(section_element.attrib)

    extracted_leaf_rows: list[RawSectionLeafRow] = []
    for child_element in list(section_element):
        child_rows = list(child_element)
        if child_rows:
            extracted_leaf_rows.extend(
                _job_raw_collect_section_leaf_rows(
                    child_element,
                    parent_attributes=inherited_attributes,
                )
            )
            continue

        leaf_payload = dict(inherited_attributes)
        leaf_payload.update(child_element.attrib)
        extracted_leaf_rows.append(
            RawSectionLeafRow(
                row_element=child_element,
                row_payload=dict(sorted(leaf_payload.items())),
            )
        )
    return extracted_leaf_rows


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
    return domain_flex_parse_local_date(value)


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
