"""Regression tests for Task 2 Alembic migration baseline."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from alembic import command
from alembic.config import Config


def _migration_build_database_url(base_url: str, database_name: str) -> str:
    """Build a SQLAlchemy URL with a replaced database name.

    Args:
        base_url: Base SQLAlchemy URL.
        database_name: Target database name.

    Returns:
        str: SQLAlchemy URL for the target database.

    Raises:
        ValueError: Raised when URL parsing fails.
    """

    parsed_url: URL = make_url(base_url)
    return str(parsed_url.set(database=database_name))


def _migration_create_database(admin_url: str, database_name: str) -> None:
    """Create a temporary PostgreSQL database for migration tests.

    Args:
        admin_url: SQLAlchemy URL for admin database access.
        database_name: Database name to create.

    Returns:
        None: The database is created as a side effect.

    Raises:
        RuntimeError: Raised when database creation fails.
    """

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        engine.dispose()


def _migration_resolve_reachable_base_url() -> str:
    """Resolve a reachable PostgreSQL URL for migration validation.

    Returns:
        str: Reachable SQLAlchemy PostgreSQL URL.

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

    last_exception_message = ""
    for candidate_url in candidate_urls:
        engine = create_engine(candidate_url)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return candidate_url
        except SQLAlchemyError as error:  # pragma: no cover - best-effort environment probe
            last_exception_message = str(error)
        finally:
            engine.dispose()

    pytest.skip(f"No reachable PostgreSQL URL for migration test. Last error: {last_exception_message}")
    return ""


def _migration_drop_database(admin_url: str, database_name: str) -> None:
    """Drop the temporary PostgreSQL database used for migration tests.

    Args:
        admin_url: SQLAlchemy URL for admin database access.
        database_name: Database name to drop.

    Returns:
        None: The database is dropped as a side effect.

    Raises:
        RuntimeError: Raised when database drop fails.
    """

    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
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
        engine.dispose()


def test_migrations_apply_and_are_idempotent() -> None:
    """Apply migrations on a fresh DB and verify idempotent re-run.

    Returns:
        None: Assertions validate migration behavior.

    Raises:
        AssertionError: Raised when expected migration artifacts are missing.
    """

    base_url = _migration_resolve_reachable_base_url()
    temp_database_name = f"test_migrations_{uuid.uuid4().hex[:10]}"
    admin_url = _migration_build_database_url(base_url, "postgres")
    temp_database_url = _migration_build_database_url(base_url, temp_database_name)

    _migration_create_database(admin_url=admin_url, database_name=temp_database_name)

    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = temp_database_url

    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")
        command.upgrade(alembic_config, "head")

        verification_engine = create_engine(temp_database_url)
        try:
            inspector = inspect(verification_engine)
            table_names = set(inspector.get_table_names())
            expected_table_names = {
                "instrument",
                "label",
                "instrument_label",
                "note",
                "ingestion_run",
                "raw_record",
                "event_trade_fill",
                "event_cashflow",
                "event_fx",
                "event_corp_action",
                "position_lot",
                "pnl_snapshot_daily",
                "alembic_version",
            }
            assert expected_table_names.issubset(table_names)

            with verification_engine.connect() as connection:
                constraint_rows = connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conname IN ("
                        "'uq_event_trade_fill_account_exec',"
                        "'uq_event_cashflow_account_txn_action_ccy',"
                        "'uq_event_fx_account_txn_ccy_pair',"
                        "'uq_event_corp_action_account_action'"
                        ")"
                    )
                ).fetchall()
            constraint_names = {row[0] for row in constraint_rows}
            assert "uq_event_trade_fill_account_exec" in constraint_names
            assert "uq_event_cashflow_account_txn_action_ccy" in constraint_names
            assert "uq_event_fx_account_txn_ccy_pair" in constraint_names
            assert "uq_event_corp_action_account_action" in constraint_names
        finally:
            verification_engine.dispose()
    finally:
        if previous_database_url is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _migration_drop_database(admin_url=admin_url, database_name=temp_database_name)
