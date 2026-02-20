"""Shared Flex payload validation helpers for ingestion and extraction workflows."""

from __future__ import annotations

import xml.etree.ElementTree as element_tree


def job_flex_validate_statements_count_contract(
    root: element_tree.Element,
    statements: list[element_tree.Element],
) -> None:
    """Validate optional FlexStatements `count` contract against parsed statement rows.

    Args:
        root: Parsed payload XML root element.
        statements: Parsed `FlexStatement` nodes.

    Returns:
        None: Raises when malformed contract is detected.

    Raises:
        ValueError: Raised when `count` is invalid or does not match parsed statements.
    """

    statements_container = _job_flex_resolve_statements_container(root=root)
    if statements_container is None:
        return

    raw_count_value = statements_container.attrib.get("count")
    if raw_count_value is None:
        return

    normalized_count_value = raw_count_value.strip()
    if not normalized_count_value:
        raise ValueError("FlexStatements count attribute must not be blank")

    try:
        expected_count = int(normalized_count_value)
    except ValueError as error:
        raise ValueError("FlexStatements count attribute must be an integer") from error

    if expected_count < 0:
        raise ValueError("FlexStatements count attribute must be >= 0")

    actual_count = len(statements)
    if expected_count != actual_count:
        raise ValueError(
            "FlexStatements count attribute does not match FlexStatement nodes "
            f"(expected={expected_count}, actual={actual_count})"
        )


def job_flex_parse_payload_with_statements(payload_bytes: bytes) -> tuple[element_tree.Element, list[element_tree.Element]]:
    """Parse payload XML and return validated FlexStatement nodes.

    Args:
        payload_bytes: Raw immutable Flex payload bytes.

    Returns:
        tuple[element_tree.Element, list[element_tree.Element]]: Parsed XML root and statement nodes.

    Raises:
        ValueError: Raised when payload is empty, malformed, or missing statements.
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
    job_flex_validate_statements_count_contract(root=root, statements=statements)
    return root, statements


def _job_flex_resolve_statements_container(root: element_tree.Element) -> element_tree.Element | None:
    """Resolve `FlexStatements` container node from parsed payload root.

    Args:
        root: Parsed payload XML root element.

    Returns:
        element_tree.Element | None: Statements container when present.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    if root.tag == "FlexStatements":
        return root

    return root.find(".//FlexStatements")


__all__ = [
    "job_flex_parse_payload_with_statements",
    "job_flex_validate_statements_count_contract",
]
