"""Regression tests for Task 5 canonical DB UPSERT behavior."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from alembic import command
from alembic.config import Config

from app.db import db_create_engine
from app.db.canonical_persistence import (
    CanonicalCashflowUpsertRequest,
    CanonicalTradeFillUpsertRequest,
    SQLAlchemyCanonicalPersistenceService,
)
from app.config import config_load_settings
from app.db.raw_persistence import SQLAlchemyRawPersistenceService
from app.db.interfaces import RawArtifactPersistRequest, RawArtifactReference, RawRecordPersistRequest


def _upsert_build_database_url(base_url: str, database_name: str) -> str:
    """Build database URL using a replaced database name.

    Args:
        base_url: Source SQLAlchemy URL.
        database_name: Database name override.

    Returns:
        str: SQLAlchemy URL targeting the requested database.

    Raises:
        ValueError: Raised when URL parsing fails.
    """

    parsed_url: URL = make_url(base_url)
    return str(parsed_url.set(database=database_name))


def _upsert_resolve_reachable_base_url() -> str:
    """Resolve one reachable PostgreSQL URL for integration tests.

    Returns:
        str: Reachable URL.

    Raises:
        pytest.skip.Exception: Raised when no candidate URL is reachable.
    """

    configured_database_url = os.getenv("DATABASE_URL")
    candidate_urls = []
    if configured_database_url:
        candidate_urls.append(configured_database_url)
    candidate_urls.extend(
        [
            "postgresql+psycopg://postgres:postgres@localhost:5433/postgres",
            "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
            "postgresql+psycopg:///postgres",
        ]
    )

    for candidate_url in candidate_urls:
        probe_engine = create_engine(candidate_url)
        try:
            with probe_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return candidate_url
        except SQLAlchemyError:
            continue
        finally:
            probe_engine.dispose()

    pytest.skip("No reachable PostgreSQL URL for canonical upsert integration tests")
    return ""


def _upsert_create_database(admin_url: str, database_name: str) -> None:
    """Create test database for canonical upsert integration tests.

    Args:
        admin_url: PostgreSQL admin URL.
        database_name: Database name to create.

    Returns:
        None: Side-effect creates database.

    Raises:
        RuntimeError: Raised when DB creation fails.
    """

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin_engine.dispose()


def _upsert_drop_database(admin_url: str, database_name: str) -> None:
    """Drop test database for canonical upsert integration tests.

    Args:
        admin_url: PostgreSQL admin URL.
        database_name: Database name to drop.

    Returns:
        None: Side-effect drops database.

    Raises:
        RuntimeError: Raised when DB drop fails.
    """

    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
    finally:
        admin_engine.dispose()


def _upsert_seed_dependencies(engine) -> tuple[str, str, str, str, str]:
    """Insert required foreign-key dependencies for canonical event rows.

    Args:
        engine: SQLAlchemy engine.

    Returns:
        tuple[str, str, str, str, str]: Account id, two run ids, raw record id, instrument id.

    Raises:
        RuntimeError: Raised when seed operations fail.
    """

    account_id = "U_TEST"
    ingestion_run_1 = str(uuid.uuid4())
    ingestion_run_2 = str(uuid.uuid4())
    raw_artifact_id = str(uuid.uuid4())
    raw_record_id = str(uuid.uuid4())
    instrument_id = str(uuid.uuid4())

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ingestion_run ("
                "ingestion_run_id, account_id, run_type, status, period_key, flex_query_id, "
                "started_at_utc, ended_at_utc"
                ") VALUES ("
                ":run_id, :account_id, 'manual', 'success', '2026-02-14', 'query', now(), now()"
                ")"
            ),
            {"run_id": ingestion_run_1, "account_id": account_id},
        )
        connection.execute(
            text(
                "INSERT INTO ingestion_run ("
                "ingestion_run_id, account_id, run_type, status, period_key, flex_query_id, "
                "started_at_utc, ended_at_utc"
                ") VALUES ("
                ":run_id, :account_id, 'reprocess', 'success', '2026-02-14', 'query', now(), now()"
                ")"
            ),
            {"run_id": ingestion_run_2, "account_id": account_id},
        )
        connection.execute(
            text(
                "INSERT INTO raw_artifact ("
                "raw_artifact_id, ingestion_run_id, account_id, period_key, flex_query_id, payload_sha256, source_payload"
                ") VALUES ("
                ":raw_artifact_id, :ingestion_run_id, :account_id, '2026-02-14', 'query', 'sha', :source_payload"
                ")"
            ),
            {
                "raw_artifact_id": raw_artifact_id,
                "ingestion_run_id": ingestion_run_1,
                "account_id": account_id,
                "source_payload": b"payload",
            },
        )
        connection.execute(
            text(
                "INSERT INTO raw_record ("
                "raw_record_id, raw_artifact_id, ingestion_run_id, account_id, period_key, flex_query_id, payload_sha256, "
                "section_name, source_row_ref, source_payload"
                ") VALUES ("
                ":raw_record_id, :raw_artifact_id, :ingestion_run_id, :account_id, '2026-02-14', 'query', 'sha', "
                "'Trades', 'Trades:Trade:transactionID=1', '{\"x\":1}'::jsonb"
                ")"
            ),
            {
                "raw_record_id": raw_record_id,
                "raw_artifact_id": raw_artifact_id,
                "ingestion_run_id": ingestion_run_1,
                "account_id": account_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO instrument ("
                "instrument_id, account_id, conid, symbol, local_symbol, isin, cusip, figi, "
                "asset_category, currency, description"
                ") VALUES ("
                ":instrument_id, :account_id, '265598', 'AAPL', 'AAPL', NULL, NULL, NULL, 'STK', 'USD', 'Apple'"
                ")"
            ),
            {"instrument_id": instrument_id, "account_id": account_id},
        )

    return account_id, ingestion_run_1, ingestion_run_2, raw_record_id, instrument_id


def test_db_canonical_trade_fill_upsert_preserves_origin_run() -> None:
    """Keep earliest ingestion_run_id while upserting mutable numeric trade fields.

    Returns:
        None: Assertions validate trade collision rule.

    Raises:
        AssertionError: Raised when upsert behavior diverges from frozen rule.
    """

    base_url = _upsert_resolve_reachable_base_url()
    database_name = f"test_canonical_upsert_trade_{uuid.uuid4().hex[:10]}"
    admin_url = _upsert_build_database_url(base_url, "postgres")
    test_database_url = _upsert_build_database_url(base_url, database_name)

    _upsert_create_database(admin_url=admin_url, database_name=database_name)
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_database_url

    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")

        engine = db_create_engine(database_url=test_database_url)
        account_id, run_id_1, run_id_2, raw_record_id, instrument_id = _upsert_seed_dependencies(engine)
        service = SQLAlchemyCanonicalPersistenceService(engine=engine)

        service.db_canonical_trade_fill_upsert(
            CanonicalTradeFillUpsertRequest(
                account_id=account_id,
                instrument_id=instrument_id,
                ingestion_run_id=run_id_1,
                source_raw_record_id=raw_record_id,
                ib_exec_id="EXEC-1",
                transaction_id="TX-1",
                trade_timestamp_utc="2026-02-14T10:00:00+00:00",
                report_date_local="2026-02-14",
                side="BUY",
                quantity="10",
                price="100",
                cost="1000",
                commission="1.0",
                fees="0",
                realized_pnl="0",
                net_cash="-1001",
                net_cash_in_base="-1001",
                fx_rate_to_base="1",
                currency="USD",
                functional_currency="USD",
            )
        )
        service.db_canonical_trade_fill_upsert(
            CanonicalTradeFillUpsertRequest(
                account_id=account_id,
                instrument_id=instrument_id,
                ingestion_run_id=run_id_2,
                source_raw_record_id=raw_record_id,
                ib_exec_id="EXEC-1",
                transaction_id="TX-1",
                trade_timestamp_utc="2026-02-14T10:00:00+00:00",
                report_date_local="2026-02-14",
                side="BUY",
                quantity="10",
                price="100",
                cost="1000",
                commission="2.5",
                fees="0",
                realized_pnl="0",
                net_cash="-1002.5",
                net_cash_in_base="-1002.5",
                fx_rate_to_base="1",
                currency="USD",
                functional_currency="USD",
            )
        )

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT ingestion_run_id::text AS ingestion_run_id, commission::text AS commission "
                    "FROM event_trade_fill WHERE account_id = :account_id AND ib_exec_id = :ib_exec_id"
                ),
                {"account_id": account_id, "ib_exec_id": "EXEC-1"},
            ).mappings().one()

        assert row["ingestion_run_id"] == run_id_1
        assert row["commission"] == "2.50000000"
    finally:
        if previous_database_url is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _upsert_drop_database(admin_url=admin_url, database_name=database_name)


def test_db_raw_record_insert_many_returns_correct_counts() -> None:
    """Persist raw rows in batch and report inserted versus deduplicated counts.

    Returns:
        None: Assertions validate raw row batch persistence contract.

    Raises:
        AssertionError: Raised when insert or dedupe counters are incorrect.
    """

    settings = config_load_settings()
    engine = db_create_engine(database_url=settings.database_url)
    raw_persistence_service = SQLAlchemyRawPersistenceService(engine=engine)
    ingestion_run_id = str(uuid.uuid4())
    account_id = f"U_TEST_{uuid.uuid4().hex[:8]}"

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ingestion_run ("
                    "ingestion_run_id, account_id, run_type, status, period_key, flex_query_id, "
                    "started_at_utc, ended_at_utc"
                    ") VALUES ("
                    ":run_id, :account_id, 'manual', 'success', '2026-02-20', 'query', now(), now()"
                    ")"
                ),
                {"run_id": ingestion_run_id, "account_id": account_id},
            )

        artifact_result = raw_persistence_service.db_raw_artifact_upsert(
            RawArtifactPersistRequest(
                ingestion_run_id=ingestion_run_id,
                reference=RawArtifactReference(
                    account_id=account_id,
                    period_key="2026-02-20",
                    flex_query_id="query",
                    payload_sha256=f"sha256-{uuid.uuid4().hex}",
                    report_date_local=None,
                ),
                source_payload=b"payload",
            )
        )

        insert_requests = [
            RawRecordPersistRequest(
                ingestion_run_id=ingestion_run_id,
                raw_artifact_id=artifact_result.artifact.raw_artifact_id,
                artifact_reference=artifact_result.artifact.reference,
                report_date_local=None,
                section_name="Trades",
                source_row_ref="Trades:Trade:transactionID=1",
                source_payload={"transactionID": "1"},
            ),
            RawRecordPersistRequest(
                ingestion_run_id=ingestion_run_id,
                raw_artifact_id=artifact_result.artifact.raw_artifact_id,
                artifact_reference=artifact_result.artifact.reference,
                report_date_local=None,
                section_name="Trades",
                source_row_ref="Trades:Trade:transactionID=2",
                source_payload={"transactionID": "2"},
            ),
        ]

        first_insert_result = raw_persistence_service.db_raw_record_insert_many(insert_requests)
        second_insert_result = raw_persistence_service.db_raw_record_insert_many(insert_requests)

        assert first_insert_result.inserted_count == 2
        assert first_insert_result.deduplicated_count == 0
        assert second_insert_result.inserted_count == 0
        assert second_insert_result.deduplicated_count == 2
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM raw_record WHERE ingestion_run_id = :run_id"), {"run_id": ingestion_run_id})
            connection.execute(text("DELETE FROM raw_artifact WHERE ingestion_run_id = :run_id"), {"run_id": ingestion_run_id})
            connection.execute(text("DELETE FROM ingestion_run WHERE ingestion_run_id = :run_id"), {"run_id": ingestion_run_id})


def test_db_canonical_cashflow_upsert_marks_correction_on_changed_amount() -> None:
    """Set correction marker when duplicate natural key arrives with different amount/date.

    Returns:
        None: Assertions validate cashflow correction collision rule.

    Raises:
        AssertionError: Raised when correction behavior diverges from frozen rule.
    """

    base_url = _upsert_resolve_reachable_base_url()
    database_name = f"test_canonical_upsert_cash_{uuid.uuid4().hex[:10]}"
    admin_url = _upsert_build_database_url(base_url, "postgres")
    test_database_url = _upsert_build_database_url(base_url, database_name)

    _upsert_create_database(admin_url=admin_url, database_name=database_name)
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_database_url

    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")

        engine = db_create_engine(database_url=test_database_url)
        account_id, run_id_1, run_id_2, raw_record_id, instrument_id = _upsert_seed_dependencies(engine)
        service = SQLAlchemyCanonicalPersistenceService(engine=engine)

        service.db_canonical_cashflow_upsert(
            CanonicalCashflowUpsertRequest(
                account_id=account_id,
                instrument_id=instrument_id,
                ingestion_run_id=run_id_1,
                source_raw_record_id=raw_record_id,
                transaction_id="CF-1",
                cash_action="DIV",
                report_date_local="2026-02-14",
                effective_at_utc="2026-02-14T10:00:00+00:00",
                amount="12.5",
                amount_in_base="12.5",
                currency="USD",
                functional_currency="USD",
                withholding_tax="0",
                fees="0",
            )
        )
        service.db_canonical_cashflow_upsert(
            CanonicalCashflowUpsertRequest(
                account_id=account_id,
                instrument_id=instrument_id,
                ingestion_run_id=run_id_2,
                source_raw_record_id=raw_record_id,
                transaction_id="CF-1",
                cash_action="DIV",
                report_date_local="2026-02-15",
                effective_at_utc="2026-02-15T10:00:00+00:00",
                amount="13.0",
                amount_in_base="13.0",
                currency="USD",
                functional_currency="USD",
                withholding_tax="0",
                fees="0",
            )
        )

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT report_date_local::text AS report_date_local, amount::text AS amount, "
                    "is_correction AS is_correction "
                    "FROM event_cashflow WHERE account_id = :account_id AND transaction_id = :transaction_id"
                ),
                {"account_id": account_id, "transaction_id": "CF-1"},
            ).mappings().one()

        assert row["report_date_local"] == "2026-02-15"
        assert row["amount"] == "13.00000000"
        assert row["is_correction"] is True
    finally:
        if previous_database_url is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _upsert_drop_database(admin_url=admin_url, database_name=database_name)


def test_db_canonical_trade_fill_upsert_rejects_non_uuid_text_values() -> None:
    """Reject malformed UUID text even when string length is 36 characters.

    Returns:
        None: Assertions validate strict UUID validation.

    Raises:
        AssertionError: Raised when malformed UUID text is accepted.
    """

    service = SQLAlchemyCanonicalPersistenceService(engine=create_engine("sqlite:///:memory:"))

    with pytest.raises(ValueError, match="must be a valid UUID string"):
        service.db_canonical_trade_fill_upsert(
            CanonicalTradeFillUpsertRequest(
                account_id="U_TEST",
                instrument_id="zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz",
                ingestion_run_id=str(uuid.uuid4()),
                source_raw_record_id=str(uuid.uuid4()),
                ib_exec_id="EXEC-INVALID",
                transaction_id="TX-INVALID",
                trade_timestamp_utc="2026-02-14T10:00:00+00:00",
                report_date_local="2026-02-14",
                side="BUY",
                quantity="1",
                price="1",
                cost="1",
                commission="0",
                fees="0",
                realized_pnl="0",
                net_cash="-1",
                net_cash_in_base="-1",
                fx_rate_to_base="1",
                currency="USD",
                functional_currency="USD",
            )
        )
