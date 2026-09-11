"""Conservative execution identity and economics consistency regressions."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db import SQLAlchemyCanonicalPersistenceService
from app.db.interfaces import CanonicalTradeFillUpsertRequest
from app.db.trade_consistency import (
    TradeConsistencyError,
    db_validate_trade_consistency,
)
from app.mapping.service import (
    MappingContractViolationError,
    RawRecordForMapping,
    mapping_build_canonical_batch,
)
from test_db_canonical_upsert import _upsert_seed_dependencies
from test_ingestion_integrity_regressions import database as _database


database = _database


def _trade_request(database) -> CanonicalTradeFillUpsertRequest:
    account_id, run_id, _, raw_record_id, instrument_id = _upsert_seed_dependencies(
        database
    )
    request = CanonicalTradeFillUpsertRequest(
        account_id=account_id,
        instrument_id=instrument_id,
        ingestion_run_id=run_id,
        source_raw_record_id=raw_record_id,
        ib_exec_id="EXEC-1",
        transaction_id="TX-1",
        trade_timestamp_utc="2026-08-21T12:00:00+00:00",
        report_date_local="2026-08-21",
        side="BUY",
        quantity="2",
        price="100",
        cost="200",
        commission="1",
        fees=None,
        realized_pnl="0",
        net_cash="-201",
        net_cash_in_base="-201",
        fx_rate_to_base="1",
        currency="USD",
        functional_currency="USD",
    )
    SQLAlchemyCanonicalPersistenceService(database).db_canonical_trade_fill_upsert(
        request
    )
    return request


def _incoming(
    request: CanonicalTradeFillUpsertRequest, **changes: object
) -> CanonicalTradeFillUpsertRequest:
    return replace(request, source_raw_record_id=str(uuid4()), **changes)


def _execution_trade(ib_exec_id: str) -> RawRecordForMapping:
    return RawRecordForMapping(
        raw_record_id=uuid4(),
        ingestion_run_id=uuid4(),
        section_name="Trades",
        source_row_ref="Trades:Trade:transactionID=37400900364",
        report_date_local=date(2026, 8, 21),
        source_payload={
            "levelOfDetail": "EXECUTION",
            "ibExecID": ib_exec_id,
            "transactionID": "37400900364",
            "tradeID": "9921",
            "conid": "265598",
            "buySell": "BUY",
            "quantity": "10",
            "tradePrice": "101",
            "currency": "USD",
            "reportDate": "2026-08-21",
            "dateTime": "2026-08-21T10:00:00+00:00",
        },
    )


def _seed_raw_trade_identity(
    database,
    request: CanonicalTradeFillUpsertRequest,
    transaction_id: str,
    *,
    successful_origin: bool,
    successful_completion: bool = False,
    row_tag: str = "Trade",
) -> str:
    owner_run_id = str(uuid4())
    completion_run_id = str(uuid4()) if successful_completion else None
    raw_artifact_id = str(uuid4())
    raw_record_id = str(uuid4())
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ingestion_run ("
                "ingestion_run_id, account_id, run_type, status, period_key, flex_query_id, "
                "started_at_utc, ended_at_utc) VALUES ("
                "CAST(:run_id AS uuid), :account_id, 'manual', :status, '2026-08', 'query', now(), now())"
            ),
            {
                "run_id": owner_run_id,
                "account_id": request.account_id,
                "status": "success" if successful_origin else "failed",
            },
        )
        if completion_run_id is not None:
            connection.execute(
                text(
                    "INSERT INTO ingestion_run ("
                    "ingestion_run_id, account_id, run_type, status, period_key, flex_query_id, "
                    "started_at_utc, ended_at_utc) VALUES ("
                    "CAST(:run_id AS uuid), :account_id, 'reprocess', 'success', "
                    "'2026-08', 'query', now(), now())"
                ),
                {"run_id": completion_run_id, "account_id": request.account_id},
            )
        connection.execute(
            text(
                "INSERT INTO raw_artifact ("
                "raw_artifact_id, ingestion_run_id, account_id, period_key, flex_query_id, "
                "payload_sha256, source_payload, completed_ingestion_run_id) VALUES ("
                "CAST(:artifact_id AS uuid), CAST(:owner_run_id AS uuid), :account_id, "
                "'2026-08', 'query', :payload_sha256, 'payload', CAST(:completion_run_id AS uuid))"
            ),
            {
                "artifact_id": raw_artifact_id,
                "owner_run_id": owner_run_id,
                "account_id": request.account_id,
                "payload_sha256": raw_artifact_id,
                "completion_run_id": completion_run_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO raw_record ("
                "raw_record_id, raw_artifact_id, ingestion_run_id, account_id, period_key, "
                "flex_query_id, payload_sha256, section_name, source_row_ref, source_payload) VALUES ("
                "CAST(:raw_record_id AS uuid), CAST(:artifact_id AS uuid), CAST(:owner_run_id AS uuid), "
                ":account_id, '2026-08', 'query', :payload_sha256, 'Trades', :source_row_ref, "
                "CAST(:source_payload AS jsonb))"
            ),
            {
                "raw_record_id": raw_record_id,
                "artifact_id": raw_artifact_id,
                "owner_run_id": owner_run_id,
                "account_id": request.account_id,
                "payload_sha256": raw_artifact_id,
                "source_row_ref": f"Trades:{row_tag}:transactionID={transaction_id}",
                "source_payload": json.dumps(
                    {
                        "levelOfDetail": "EXECUTION",
                        "ibExecID": request.ib_exec_id,
                        "transactionID": transaction_id,
                    }
                ),
            },
        )
    return raw_record_id


@pytest.mark.parametrize("sentinel", ["-", "--", "N/A"])
def test_mapping_rejects_explicit_execution_id_null_sentinels(sentinel: str) -> None:
    with pytest.raises(MappingContractViolationError, match="ibExecID"):
        mapping_build_canonical_batch("U_TEST", "USD", [_execution_trade(sentinel)])


def test_mapping_keeps_transaction_fallback_for_blank_execution_id() -> None:
    batch = mapping_build_canonical_batch("U_TEST", "USD", [_execution_trade("")])

    assert batch.trade_fill_requests[0].ib_exec_id == "FLEX_TXN:37400900364"


def test_validator_rejects_stored_execution_identity_changes(database) -> None:
    original = _trade_request(database)

    changed_execution = _incoming(original, ib_exec_id="EXEC-2")
    with database.begin() as connection, pytest.raises(TradeConsistencyError) as caught:
        db_validate_trade_consistency(connection, [changed_execution])
    assert caught.value.code == "TRADE_CONSISTENCY_CONFLICT"
    assert caught.value.conflicting_fields == ("ib_exec_id",)
    assert set(caught.value.source_raw_record_ids) == {
        original.source_raw_record_id,
        changed_execution.source_raw_record_id,
    }

    changed_transaction = _incoming(original, transaction_id="TX-2")
    with database.begin() as connection, pytest.raises(TradeConsistencyError) as caught:
        db_validate_trade_consistency(connection, [changed_transaction])
    assert caught.value.conflicting_fields == ("transaction_id",)

    with database.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM event_trade_fill")) == 1


def test_validator_rejects_every_protected_execution_field_change(database) -> None:
    original = _trade_request(database)
    protected_changes = {
        "instrument_id": str(uuid4()),
        "side": "SELL",
        "quantity": "3",
        "trade_timestamp_utc": "2026-08-21T12:00:01+00:00",
        "currency": "EUR",
        "price": "101",
        "commission": "2",
        "fees": "1",
        "net_cash": "-202",
    }

    for field_name, changed_value in protected_changes.items():
        with (
            database.begin() as connection,
            pytest.raises(TradeConsistencyError) as caught,
        ):
            db_validate_trade_consistency(
                connection, [_incoming(original, **{field_name: changed_value})]
            )
        assert caught.value.conflicting_fields == (field_name,)


def test_validator_rejects_conflicting_duplicates_within_one_batch(database) -> None:
    original = _trade_request(database)
    first = _incoming(original, ib_exec_id="BATCH-1", transaction_id="BATCH-TX")
    changed_execution = _incoming(first, ib_exec_id="BATCH-2")
    with database.begin() as connection, pytest.raises(TradeConsistencyError) as caught:
        db_validate_trade_consistency(connection, [first, changed_execution])
    assert caught.value.conflicting_fields == ("ib_exec_id",)
    assert set(caught.value.source_raw_record_ids) == {
        first.source_raw_record_id,
        changed_execution.source_raw_record_id,
    }

    first = _incoming(original, ib_exec_id="BATCH-3", transaction_id="BATCH-TX-1")
    changed_transaction = _incoming(first, transaction_id="BATCH-TX-2")
    with database.begin() as connection, pytest.raises(TradeConsistencyError) as caught:
        db_validate_trade_consistency(connection, [first, changed_transaction])
    assert caught.value.conflicting_fields == ("transaction_id",)

    changed_price = _incoming(first, price="999")
    with database.begin() as connection, pytest.raises(TradeConsistencyError) as caught:
        db_validate_trade_consistency(connection, [first, changed_price])
    assert caught.value.conflicting_fields == ("price",)


def test_validator_accepts_normalized_economics_and_derived_refreshes(database) -> None:
    original = _trade_request(database)
    equivalent = _incoming(
        original,
        quantity="2.00000000",
        price="100.000",
        commission="1.0000",
        fees="0.00000000",
        net_cash="-201.00",
        trade_timestamp_utc="2026-08-21T14:00:00+02:00",
        report_date_local="2026-08-22",
        cost="250",
        realized_pnl="17",
        net_cash_in_base="-190",
        fx_rate_to_base="1.1",
        functional_currency="EUR",
    )

    with database.begin() as connection:
        db_validate_trade_consistency(connection, [equivalent])

    no_costs = replace(
        original,
        ib_exec_id="EXEC-NO-COSTS",
        transaction_id="TX-NO-COSTS",
        commission=None,
        fees=None,
    )
    SQLAlchemyCanonicalPersistenceService(database).db_canonical_trade_fill_upsert(
        no_costs
    )
    with database.begin() as connection:
        db_validate_trade_consistency(
            connection,
            [_incoming(no_costs, commission="0.000", fees="0")],
        )


def test_validator_compares_protected_numerics_at_postgresql_scale(database) -> None:
    seeded = _trade_request(database)
    extra_scale = replace(
        seeded,
        ib_exec_id="EXEC-EXTRA-SCALE",
        transaction_id="TX-EXTRA-SCALE",
        quantity="2.123456785",
        price="100.123456789",
        commission="1.123456785",
        fees="0.123456785",
        net_cash="-201.123456785",
    )
    SQLAlchemyCanonicalPersistenceService(database).db_canonical_trade_fill_upsert(
        extra_scale
    )
    equivalent_batch_row = _incoming(
        extra_scale,
        quantity="2.123456786",
        price="100.123456791",
        commission="1.123456786",
        fees="0.123456786",
        net_cash="-201.123456786",
    )

    with database.begin() as connection:
        db_validate_trade_consistency(
            connection,
            [_incoming(extra_scale), equivalent_batch_row],
        )


@pytest.mark.parametrize(
    "successful_completion", [False, True], ids=["origin", "completion"]
)
def test_validator_remembers_successful_late_transaction_identity(
    database,
    successful_completion: bool,
) -> None:
    seeded = _trade_request(database)
    original = replace(
        seeded,
        ib_exec_id=f"EXEC-LATE-{successful_completion}",
        transaction_id=None,
    )
    SQLAlchemyCanonicalPersistenceService(database).db_canonical_trade_fill_upsert(
        original
    )
    late_transaction = _incoming(original, transaction_id="TX-LATE")

    with database.begin() as connection:
        db_validate_trade_consistency(connection, [late_transaction])

    evidence_raw_record_id = _seed_raw_trade_identity(
        database,
        original,
        "TX-LATE",
        successful_origin=not successful_completion,
        successful_completion=successful_completion,
    )

    with database.begin() as connection, pytest.raises(TradeConsistencyError) as caught:
        db_validate_trade_consistency(
            connection,
            [_incoming(original, transaction_id="TX-CHANGED")],
        )
    assert caught.value.conflicting_fields == ("transaction_id",)
    assert evidence_raw_record_id in caught.value.source_raw_record_ids

    with database.begin() as connection, pytest.raises(TradeConsistencyError) as caught:
        db_validate_trade_consistency(
            connection,
            [_incoming(original, ib_exec_id="EXEC-CHANGED", transaction_id="TX-LATE")],
        )
    assert caught.value.conflicting_fields == ("ib_exec_id",)

    with database.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT transaction_id FROM event_trade_fill "
                    "WHERE account_id=:account_id AND ib_exec_id=:ib_exec_id"
                ),
                {"account_id": original.account_id, "ib_exec_id": original.ib_exec_id},
            )
            is None
        )


def test_validator_does_not_bind_failed_late_transaction_evidence(database) -> None:
    seeded = _trade_request(database)
    original = replace(seeded, ib_exec_id="EXEC-FAILED-EVIDENCE", transaction_id=None)
    SQLAlchemyCanonicalPersistenceService(database).db_canonical_trade_fill_upsert(
        original
    )
    _seed_raw_trade_identity(
        database,
        original,
        "TX-FAILED",
        successful_origin=False,
    )

    with database.begin() as connection:
        db_validate_trade_consistency(
            connection,
            [_incoming(original, transaction_id="TX-ACCEPTED")],
        )
        db_validate_trade_consistency(
            connection,
            [
                _incoming(
                    original,
                    ib_exec_id="EXEC-OTHER",
                    transaction_id="TX-FAILED",
                )
            ],
        )


def test_validator_ignores_successful_non_trade_raw_identity(database) -> None:
    seeded = _trade_request(database)
    original = replace(seeded, ib_exec_id="EXEC-ORDER-EVIDENCE", transaction_id=None)
    SQLAlchemyCanonicalPersistenceService(database).db_canonical_trade_fill_upsert(
        original
    )
    _seed_raw_trade_identity(
        database,
        original,
        "TX-ORDER",
        successful_origin=True,
        row_tag="Order",
    )

    with database.begin() as connection:
        db_validate_trade_consistency(
            connection,
            [_incoming(original, transaction_id="TX-OTHER")],
        )
        db_validate_trade_consistency(
            connection,
            [
                _incoming(
                    original,
                    ib_exec_id="EXEC-OTHER",
                    transaction_id="TX-ORDER",
                )
            ],
        )
