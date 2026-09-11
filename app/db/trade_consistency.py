"""Database checks for immutable execution identity and economics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
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

    _db_validate_incoming_trade_consistency(requests)
    stored_rows = _db_trade_rows_for_incoming_identities(connection, requests)
    stored_by_execution: dict[tuple[str, str], list[dict[str, Any]]] = {}
    stored_by_transaction: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in stored_rows:
        account_id = _normalized_text(row["account_id"])
        stored_by_execution.setdefault(
            (account_id, _normalized_text(row["ib_exec_id"])), []
        ).append(row)
        transaction_id = _normalized_optional_text(row["transaction_id"])
        if transaction_id is not None:
            stored_by_transaction.setdefault((account_id, transaction_id), []).append(
                row
            )

    for request in requests:
        account_id = _normalized_text(request.account_id)
        ib_exec_id = _normalized_text(request.ib_exec_id)
        transaction_id = _normalized_optional_text(request.transaction_id)
        matching_rows = list(stored_by_execution.get((account_id, ib_exec_id), ()))
        if transaction_id is not None:
            for row in stored_by_transaction.get((account_id, transaction_id), ()):
                if row not in matching_rows:
                    matching_rows.append(row)

        conflicting_fields: list[str] = []
        source_raw_record_ids = [request.source_raw_record_id]
        for row in matching_rows:
            source_raw_record_ids.append(str(row["source_raw_record_id"]))
            stored_ib_exec_id = _normalized_text(row["ib_exec_id"])
            if transaction_id is not None and stored_ib_exec_id != ib_exec_id:
                conflicting_fields.append("ib_exec_id")
                continue

            stored_transaction_id = _normalized_optional_text(row["transaction_id"])
            if (
                stored_ib_exec_id == ib_exec_id
                and transaction_id is not None
                and stored_transaction_id is not None
                and transaction_id != stored_transaction_id
            ):
                conflicting_fields.append("transaction_id")

            if stored_ib_exec_id == ib_exec_id:
                conflicting_fields.extend(_conflicting_protected_fields(row, request))

        if conflicting_fields:
            raise TradeConsistencyError(
                identity=_trade_identity(request),
                conflicting_fields=_ordered_fields(conflicting_fields),
                source_raw_record_ids=source_raw_record_ids,
            )


def _db_validate_incoming_trade_consistency(
    requests: list[CanonicalTradeFillUpsertRequest],
) -> None:
    by_execution: dict[tuple[str, str], list[CanonicalTradeFillUpsertRequest]] = {}
    by_transaction: dict[tuple[str, str], list[CanonicalTradeFillUpsertRequest]] = {}
    for request in requests:
        account_id = _normalized_text(request.account_id)
        by_execution.setdefault(
            (account_id, _normalized_text(request.ib_exec_id)), []
        ).append(request)
        transaction_id = _normalized_optional_text(request.transaction_id)
        if transaction_id is not None:
            by_transaction.setdefault((account_id, transaction_id), []).append(request)

    for grouped_requests in by_transaction.values():
        if (
            len({_normalized_text(request.ib_exec_id) for request in grouped_requests})
            > 1
        ):
            _raise_incoming_conflict(grouped_requests, ("ib_exec_id",))

    for grouped_requests in by_execution.values():
        nonempty_transaction_ids = {
            transaction_id
            for request in grouped_requests
            if (transaction_id := _normalized_optional_text(request.transaction_id))
            is not None
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
    requests: list[CanonicalTradeFillUpsertRequest],
) -> list[dict[str, Any]]:
    identities = [
        {
            "account_id": _normalized_text(request.account_id),
            "ib_exec_id": _normalized_text(request.ib_exec_id),
            "transaction_id": _normalized_optional_text(request.transaction_id),
        }
        for request in requests
    ]
    rows = connection.execute(
        text(
            "WITH incoming AS ("
            "SELECT account_id, ib_exec_id, transaction_id "
            "FROM jsonb_to_recordset(CAST(:identities_json AS jsonb)) "
            "AS identity(account_id text, ib_exec_id text, transaction_id text)"
            ") SELECT DISTINCT trade.event_trade_fill_id, trade.account_id, "
            "trade.instrument_id::text AS instrument_id, trade.source_raw_record_id, "
            "trade.ib_exec_id, trade.transaction_id, trade.trade_timestamp_utc, trade.side, "
            "trade.quantity, trade.price, trade.commission, trade.fees, trade.net_cash, trade.currency "
            "FROM event_trade_fill trade JOIN incoming "
            "ON trade.account_id = incoming.account_id "
            "AND (trade.ib_exec_id = incoming.ib_exec_id "
            "OR (incoming.transaction_id IS NOT NULL AND trade.transaction_id = incoming.transaction_id)) "
            "ORDER BY trade.account_id, trade.ib_exec_id, trade.event_trade_fill_id"
        ),
        {"identities_json": json.dumps(identities)},
    ).mappings()
    return [dict(row) for row in rows]


def _raise_incoming_conflict(
    requests: list[CanonicalTradeFillUpsertRequest],
    conflicting_fields: Iterable[str],
) -> None:
    raise TradeConsistencyError(
        identity=_trade_identity(requests[0]),
        conflicting_fields=conflicting_fields,
        source_raw_record_ids=(request.source_raw_record_id for request in requests),
    )


def _conflicting_protected_fields(
    first: CanonicalTradeFillUpsertRequest | dict[str, Any],
    second: CanonicalTradeFillUpsertRequest | dict[str, Any],
) -> list[str]:
    return [
        field_name
        for field_name in _PROTECTED_FIELDS
        if _normalized_field_value(field_name, _field_value(first, field_name))
        != _normalized_field_value(field_name, _field_value(second, field_name))
    ]


def _field_value(
    value: CanonicalTradeFillUpsertRequest | dict[str, Any], field_name: str
) -> Any:
    if isinstance(value, dict):
        return value[field_name]
    return getattr(value, field_name)


def _normalized_field_value(field_name: str, value: Any) -> Any:
    if field_name in _NUMERIC_FIELDS:
        if value is None or (isinstance(value, str) and not value.strip()):
            return Decimal("0") if field_name in _ZERO_WHEN_MISSING_FIELDS else None
        return Decimal(str(value).strip())
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


def _trade_identity(request: CanonicalTradeFillUpsertRequest) -> str:
    return (
        f"account_id={_normalized_text(request.account_id)} "
        f"ib_exec_id={_normalized_text(request.ib_exec_id)} "
        f"transaction_id={_normalized_optional_text(request.transaction_id)}"
    )


__all__ = ["TradeConsistencyError", "db_validate_trade_consistency"]
