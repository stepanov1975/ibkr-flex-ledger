"""Migration regressions for durable broker account identity metadata."""

from __future__ import annotations

import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text

from test_db_migrations import (
    _migration_build_database_url,
    _migration_create_database,
    _migration_drop_database,
    _migration_resolve_reachable_base_url,
)


@pytest.fixture
def database_before_broker_identity_migration(monkeypatch):
    base_url = _migration_resolve_reachable_base_url()
    database_name = f"test_broker_identity_{uuid.uuid4().hex[:10]}"
    admin_url = _migration_build_database_url(base_url, "postgres")
    database_url = _migration_build_database_url(base_url, database_name)
    _migration_create_database(admin_url, database_name)
    monkeypatch.setenv("DATABASE_URL", database_url)
    alembic_config = Config("alembic.ini")
    try:
        command.upgrade(alembic_config, "20260911_16")
        yield database_url, alembic_config
    finally:
        _migration_drop_database(admin_url, database_name)


def _insert_artifact(connection, *, status: str, payload: bytes) -> uuid.UUID:
    run_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO ingestion_run (ingestion_run_id, account_id, run_type, status, period_key, "
            "flex_query_id, started_at_utc, ended_at_utc) VALUES "
            "(:run_id, 'IDENTITY', 'manual', :status, :period_key, 'query', now(), now())"
        ),
        {"run_id": run_id, "status": status, "period_key": str(artifact_id)},
    )
    connection.execute(
        text(
            "INSERT INTO raw_artifact (raw_artifact_id, ingestion_run_id, account_id, period_key, "
            "flex_query_id, payload_sha256, source_payload) VALUES "
            "(:artifact_id, :run_id, 'IDENTITY', :period_key, 'query', :payload_sha256, :payload)"
        ),
        {
            "artifact_id": artifact_id,
            "run_id": run_id,
            "period_key": str(artifact_id),
            "payload_sha256": artifact_id.hex,
            "payload": payload,
        },
    )
    return artifact_id


def test_migration_backfills_successful_identities_without_rewriting_payloads(
    database_before_broker_identity_migration,
):
    database_url, alembic_config = database_before_broker_identity_migration
    engine = create_engine(database_url)
    header_payload = (
        b'<FlexQueryResponse><FlexStatements count="1"><FlexStatement accountId="U_HEADER">'
        b'<AccountInformation currency="USD" /></FlexStatement></FlexStatements></FlexQueryResponse>'
    )
    information_payload = (
        b'<FlexQueryResponse><FlexStatements count="1"><FlexStatement>'
        b'<AccountInformation accountId="U_INFO" currency="USD" />'
        b'</FlexStatement></FlexStatements></FlexQueryResponse>'
    )
    failed_payload = b"retained malformed report"
    try:
        with engine.begin() as connection:
            header_id = _insert_artifact(connection, status="success", payload=header_payload)
            information_id = _insert_artifact(connection, status="success", payload=information_payload)
            failed_id = _insert_artifact(connection, status="failed", payload=failed_payload)
        engine.dispose()

        command.upgrade(alembic_config, "20260911_17")

        engine = create_engine(database_url)
        with engine.connect() as connection:
            rows = {
                row.raw_artifact_id: (row.broker_account_id, bytes(row.source_payload))
                for row in connection.execute(text(
                    "SELECT raw_artifact_id, broker_account_id, source_payload FROM raw_artifact"
                ))
            }
        assert rows == {
            header_id: ("U_HEADER", header_payload),
            information_id: ("U_INFO", information_payload),
            failed_id: (None, failed_payload),
        }
        engine.dispose()

        command.downgrade(alembic_config, "20260911_16")
        engine = create_engine(database_url)
        assert "broker_account_id" not in {
            column["name"] for column in inspect(engine).get_columns("raw_artifact")
        }
    finally:
        engine.dispose()


def test_migration_rejects_ambiguous_successful_identity(
    database_before_broker_identity_migration,
):
    database_url, alembic_config = database_before_broker_identity_migration
    engine = create_engine(database_url)
    ambiguous_payload = (
        b'<FlexQueryResponse><FlexStatements count="1"><FlexStatement accountId="U_ONE">'
        b'<AccountInformation accountId="U_TWO" currency="USD" />'
        b'</FlexStatement></FlexStatements></FlexQueryResponse>'
    )
    try:
        with engine.begin() as connection:
            artifact_id = _insert_artifact(connection, status="success", payload=ambiguous_payload)
        engine.dispose()

        with pytest.raises(RuntimeError, match=str(artifact_id)):
            command.upgrade(alembic_config, "20260911_17")

        engine = create_engine(database_url)
        assert "broker_account_id" not in {
            column["name"] for column in inspect(engine).get_columns("raw_artifact")
        }
    finally:
        engine.dispose()
