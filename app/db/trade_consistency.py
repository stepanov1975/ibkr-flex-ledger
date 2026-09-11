"""Database checks for immutable execution identity and economics."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import Connection, text

from app.db.interfaces import CanonicalTradeFillUpsertRequest


_PROTECTED_FIELDS = (
    "instrument_id",
    "side",
    "quantity",
    "trade_timestamp_utc",
    "currency",
    "price",
    "commission",
    "fees",
    "net_cash",
)
_NUMERIC_FIELDS = frozenset({"quantity", "price", "commission", "fees", "net_cash"})
_ZERO_WHEN_MISSING_FIELDS = frozenset({"commission", "fees"})
_PROTECTED_NUMERIC_QUANTUM = Decimal("0.00000001")


class TradeConsistencyError(ValueError):
    """Report conflicting execution identity or protected economics."""

    code = "TRADE_CONSISTENCY_CONFLICT"

    def __init__(
        self,
        *,
        identity: str,
        conflicting_fields: Iterable[str],
        source_raw_record_ids: Iterable[str],
    ) -> None:
        self.identity = identity
        self.conflicting_fields = tuple(dict.fromkeys(conflicting_fields))
        self.source_raw_record_ids = tuple(dict.fromkeys(source_raw_record_ids))
        fields = ",".join(self.conflicting_fields)
        source_ids = ",".join(self.source_raw_record_ids)
        super().__init__(
            f"{self.code} identity={identity} conflicting_fields={fields} "
            f"source_raw_record_ids={source_ids}"
        )


def db_validate_trade_consistency(
    connection: Connection,
    requests: list[CanonicalTradeFillUpsertRequest],
) -> None:
    """Reject conflicting execution identities and protected economics.

    The database lookup is batched for the full incoming request list. The
    function only validates; it never reconciles or mutates canonical rows.
    """

    if not requests:
        return

    incoming_rows = [_normalized_trade(asdict(request)) for request in requests]
    _db_validate_incoming_trade_consistency(incoming_rows)
    stored_rows = _db_trade_rows_for_incoming_identities(connection, incoming_rows)
    stored_by_execution = _index_trades(stored_rows, "ib_exec_id")
    stored_by_transaction = _index_trades(stored_rows, "transaction_id")

    for request in incoming_rows:
        account_id = request["account_id"]
        ib_exec_id = request["ib_exec_id"]
        transaction_id = request["transaction_id"]
        matching_rows = list(stored_by_execution.get((account_id, ib_exec_id), ()))
        if transaction_id is not None:
            for row in stored_by_transaction.get((account_id, transaction_id), ()):
                if row not in matching_rows:
                    matching_rows.append(row)

        conflicting_fields: list[str] = []
        source_raw_record_ids = [request["source_raw_record_id"]]
        for row in matching_rows:
            source_raw_record_ids.append(row["source_raw_record_id"])
            stored_ib_exec_id = row["ib_exec_id"]
            if transaction_id is not None and stored_ib_exec_id != ib_exec_id:
                conflicting_fields.append("ib_exec_id")
                continue

            stored_transaction_id = row["transaction_id"]
            if (
                stored_ib_exec_id == ib_exec_id
                and transaction_id is not None
                and stored_transaction_id is not None
                and transaction_id != stored_transaction_id
            ):
                conflicting_fields.append("transaction_id")

            if stored_ib_exec_id == ib_exec_id and row["evidence_type"] == "canonical":
                conflicting_fields.extend(_conflicting_protected_fields(row, request))

        if conflicting_fields:
            raise TradeConsistencyError(
                identity=_trade_identity(request),
                conflicting_fields=_ordered_fields(conflicting_fields),
                source_raw_record_ids=source_raw_record_ids,
            )


def _db_validate_incoming_trade_consistency(
    requests: list[dict[str, Any]],
) -> None:
    for grouped_requests in _index_trades(requests, "transaction_id").values():
        if len({request["ib_exec_id"] for request in grouped_requests}) > 1:
            _raise_incoming_conflict(grouped_requests, ("ib_exec_id",))

    for grouped_requests in _index_trades(requests, "ib_exec_id").values():
        nonempty_transaction_ids = {
            request["transaction_id"]
            for request in grouped_requests
            if request["transaction_id"] is not None
        }
        if len(nonempty_transaction_ids) > 1:
            _raise_incoming_conflict(grouped_requests, ("transaction_id",))

        baseline = grouped_requests[0]
        conflicting_fields: list[str] = []
        for request in grouped_requests[1:]:
            conflicting_fields.extend(_conflicting_protected_fields(baseline, request))
        if conflicting_fields:
            _raise_incoming_conflict(
                grouped_requests, _ordered_fields(conflicting_fields)
            )


def _db_trade_rows_for_incoming_identities(
    connection: Connection,
    requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    identities = [
        {field: request[field] for field in ("account_id", "ib_exec_id", "transaction_id")}
        for request in requests
    ]
    rows = connection.execute(
        text(
            "WITH incoming AS ("
            "SELECT account_id, ib_exec_id, transaction_id "
            "FROM jsonb_to_recordset(CAST(:identities_json AS jsonb)) "
            "AS identity(account_id text, ib_exec_id text, transaction_id text)"
            "), successful_raw_trade AS ("
            "SELECT raw.raw_record_id, raw.account_id, "
            "BTRIM(COALESCE(raw.source_payload->>'ibExecID', '')) AS source_ib_exec_id, "
            "CASE WHEN BTRIM(COALESCE(raw.source_payload->>'transactionID', '')) "
            "IN ('', '-', '--', 'N/A') THEN NULL "
            "ELSE BTRIM(raw.source_payload->>'transactionID') END AS transaction_id, "
            "CASE WHEN BTRIM(COALESCE(raw.source_payload->>'tradeID', '')) "
            "IN ('', '-', '--', 'N/A') THEN NULL "
            "ELSE BTRIM(raw.source_payload->>'tradeID') END AS trade_id, "
            "UPPER(BTRIM(COALESCE(raw.source_payload->>'levelOfDetail', ''))) AS level_of_detail "
            "FROM raw_record raw JOIN raw_artifact artifact USING (raw_artifact_id) "
            "JOIN ingestion_run owner ON owner.ingestion_run_id=raw.ingestion_run_id "
            "LEFT JOIN ingestion_run completion "
            "ON completion.ingestion_run_id=artifact.completed_ingestion_run_id "
            "WHERE raw.account_id IN (SELECT account_id FROM incoming) "
            "AND raw.section_name='Trades' "
            "AND raw.source_row_ref LIKE 'Trades:Trade:%' "
            "AND (owner.status='success' OR completion.status='success')"
            # Normalize JSON identities once, before matching the incoming batch.
            "), raw_identity_source AS MATERIALIZED ("
            "SELECT raw_record_id, account_id, transaction_id, "
            "CASE WHEN source_ib_exec_id IN ('-', '--', 'N/A') THEN NULL "
            "WHEN source_ib_exec_id<>'' THEN source_ib_exec_id "
            "WHEN level_of_detail='EXECUTION' AND transaction_id IS NOT NULL "
            "THEN 'FLEX_TXN:' || transaction_id "
            "WHEN level_of_detail='EXECUTION' AND trade_id IS NOT NULL "
            "THEN 'FLEX_TRADE:' || trade_id END AS ib_exec_id "
            "FROM successful_raw_trade"
            "), raw_identity AS ("
            "SELECT DISTINCT ON (raw.account_id, raw.ib_exec_id, raw.transaction_id) "
            "raw.raw_record_id, raw.account_id, raw.ib_exec_id, raw.transaction_id "
            "FROM raw_identity_source raw JOIN incoming "
            "ON raw.account_id=incoming.account_id "
            "AND (raw.ib_exec_id=incoming.ib_exec_id "
            "OR (incoming.transaction_id IS NOT NULL AND raw.transaction_id=incoming.transaction_id)) "
            "WHERE raw.ib_exec_id IS NOT NULL "
            "ORDER BY raw.account_id, raw.ib_exec_id, raw.transaction_id NULLS FIRST, raw.raw_record_id"
            ") SELECT DISTINCT 'canonical' AS evidence_type, trade.account_id, "
            "trade.instrument_id::text AS instrument_id, trade.source_raw_record_id, "
            "trade.ib_exec_id, trade.transaction_id, trade.trade_timestamp_utc, trade.side, "
            "trade.quantity, trade.price, trade.commission, trade.fees, trade.net_cash, trade.currency "
            "FROM event_trade_fill trade JOIN incoming "
            "ON trade.account_id = incoming.account_id "
            "AND (trade.ib_exec_id = incoming.ib_exec_id "
            "OR (incoming.transaction_id IS NOT NULL AND trade.transaction_id = incoming.transaction_id)) "
            "UNION ALL SELECT DISTINCT 'raw' AS evidence_type, raw.account_id, "
            "NULL::text AS instrument_id, raw.raw_record_id AS source_raw_record_id, "
            "raw.ib_exec_id, raw.transaction_id, NULL::timestamptz AS trade_timestamp_utc, "
            "NULL::text AS side, NULL::numeric AS quantity, NULL::numeric AS price, "
            "NULL::numeric AS commission, NULL::numeric AS fees, NULL::numeric AS net_cash, "
            "NULL::text AS currency FROM raw_identity raw "
            "ORDER BY account_id, ib_exec_id, source_raw_record_id"
        ),
        {"identities_json": json.dumps(identities)},
    ).mappings()
    return [_normalized_trade(dict(row)) for row in rows]


def _normalized_trade(row: dict[str, Any]) -> dict[str, Any]:
    """Use the same identity and economics representation for requests and stored evidence."""
    return {
        **row,
        "account_id": _normalized_text(row["account_id"]),
        "ib_exec_id": _normalized_text(row["ib_exec_id"]),
        "transaction_id": _normalized_optional_text(row["transaction_id"]),
        "source_raw_record_id": str(row["source_raw_record_id"]),
        **({field: _normalized_field_value(field, row[field]) for field in _PROTECTED_FIELDS}
           if row.get("evidence_type") != "raw" else {}),
    }


def _index_trades(
    rows: list[dict[str, Any]], field: str,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row[field] is not None:
            index.setdefault((row["account_id"], row[field]), []).append(row)
    return index


def _raise_incoming_conflict(
    requests: list[dict[str, Any]],
    conflicting_fields: Iterable[str],
) -> None:
    raise TradeConsistencyError(
        identity=_trade_identity(requests[0]),
        conflicting_fields=conflicting_fields,
        source_raw_record_ids=(request["source_raw_record_id"] for request in requests),
    )


def _conflicting_protected_fields(
    first: dict[str, Any],
    second: dict[str, Any],
) -> list[str]:
    return [
        field_name
        for field_name in _PROTECTED_FIELDS
        if first[field_name] != second[field_name]
    ]


def _normalized_field_value(field_name: str, value: Any) -> Any:
    if field_name in _NUMERIC_FIELDS:
        if value is None or (isinstance(value, str) and not value.strip()):
            return Decimal("0") if field_name in _ZERO_WHEN_MISSING_FIELDS else None
        return Decimal(str(value).strip()).quantize(
            _PROTECTED_NUMERIC_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    if field_name == "trade_timestamp_utc":
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    if field_name == "instrument_id":
        return UUID(str(value).strip())
    return _normalized_text(value)


def _normalized_text(value: Any) -> str:
    return str(value).strip()


def _normalized_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = _normalized_text(value)
    return normalized or None


def _ordered_fields(fields: Iterable[str]) -> tuple[str, ...]:
    field_set = set(fields)
    identity_fields = ("ib_exec_id", "transaction_id")
    return tuple(
        field for field in (*identity_fields, *_PROTECTED_FIELDS) if field in field_set
    )


def _trade_identity(request: dict[str, Any]) -> str:
    return (
        f"account_id={request['account_id']} "
        f"ib_exec_id={request['ib_exec_id']} "
        f"transaction_id={request['transaction_id']}"
    )


__all__ = ["TradeConsistencyError", "db_validate_trade_consistency"]
