"""SQL expressions for fields from persisted Flex rows."""


def db_raw_numeric(field: str, alias: str = "raw") -> str:
    """Read a nullable numeric field from a Flex row using broker null and grouping conventions."""
    return (
        f"CASE WHEN BTRIM(COALESCE({alias}.source_payload->>'{field}', '')) "
        "IN ('', '-', '--', 'N/A') THEN NULL ELSE "
        f"REPLACE(BTRIM({alias}.source_payload->>'{field}'), ',', '')::numeric END"
    )


def db_trade_is_delivery(alias: str) -> str:
    """Identify a physical assignment or exercise reported on a BookTrade row."""
    return (f"({alias}.source_payload->>'transactionType'='BookTrade' AND "
            f"{alias}.source_payload->>'notes' ~ '(^|[;,[:space:]])(A|Ex)($|[;,[:space:]])')")
