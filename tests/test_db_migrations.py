"""Regression tests for Task 2 Alembic migration baseline."""

from __future__ import annotations

from decimal import Decimal
import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from alembic import command
from alembic.config import Config

from app.db.portfolio import SQLAlchemyPortfolioService


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
    return str(parsed_url.set(database=database_name).render_as_string(hide_password=False))


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
        engine = create_engine(candidate_url, connect_args={"connect_timeout": 1})
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


def test_migrations_apply_and_are_idempotent_ingestion_indexes() -> None:
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

            raw_record_indexes = {
                index["name"]: tuple(index["column_names"])
                for index in inspector.get_indexes("raw_record")
            }
            assert raw_record_indexes["ix_raw_record_run_created_id"] == (
                "ingestion_run_id",
                "created_at_utc",
                "raw_record_id",
            )
            assert raw_record_indexes["ix_raw_record_prior_version"] == (
                "account_id",
                "flex_query_id",
                "section_name",
                "source_row_ref",
                "created_at_utc",
                "raw_record_id",
            )
            snapshot_columns = {
                column["name"] for column in inspector.get_columns("pnl_snapshot_daily")
            }
            assert "calculation_provisional" in snapshot_columns
        finally:
            verification_engine.dispose()

        command.downgrade(alembic_config, "20260821_03")
        downgraded_engine = create_engine(temp_database_url)
        try:
            downgraded_names = {
                index["name"] for index in inspect(downgraded_engine).get_indexes("raw_record")
            }
            assert "ix_raw_record_run_created_id" not in downgraded_names
            assert "ix_raw_record_prior_version" not in downgraded_names
        finally:
            downgraded_engine.dispose()
    finally:
        if previous_database_url is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _migration_drop_database(admin_url=admin_url, database_name=temp_database_name)


def test_migration_05_adds_nullable_artifact_completion_foreign_key() -> None:
    """Add and remove the completion marker while enforcing run references."""

    base_url = _migration_resolve_reachable_base_url()
    temp_database_name = f"test_artifact_completion_{uuid.uuid4().hex[:10]}"
    admin_url = _migration_build_database_url(base_url, "postgres")
    temp_database_url = _migration_build_database_url(base_url, temp_database_name)
    _migration_create_database(admin_url=admin_url, database_name=temp_database_name)

    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = temp_database_url
    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "20260821_04")
        pre_upgrade_engine = create_engine(temp_database_url)
        try:
            assert "completed_ingestion_run_id" not in {
                column["name"] for column in inspect(pre_upgrade_engine).get_columns("raw_artifact")
            }
        finally:
            pre_upgrade_engine.dispose()

        command.upgrade(alembic_config, "head")
        verification_engine = create_engine(temp_database_url)
        try:
            inspector = inspect(verification_engine)
            completion_column = next(
                column
                for column in inspector.get_columns("raw_artifact")
                if column["name"] == "completed_ingestion_run_id"
            )
            assert completion_column["nullable"] is True
            completion_links = [
                key
                for key in inspector.get_foreign_keys("raw_artifact")
                if key.get("constrained_columns") == ["completed_ingestion_run_id"]
                and key.get("referred_table") == "ingestion_run"
                and key.get("referred_columns") == ["ingestion_run_id"]
            ]
            assert len(completion_links) == 1

            owner_run_id = str(uuid.uuid4())
            completed_run_id = str(uuid.uuid4())
            artifact_id = str(uuid.uuid4())
            with verification_engine.begin() as connection:
                for run_id, status in (
                    (owner_run_id, "failed"),
                    (completed_run_id, "success"),
                ):
                    connection.execute(
                        text(
                            "INSERT INTO ingestion_run ("
                            "ingestion_run_id, account_id, run_type, status, period_key, flex_query_id, "
                            "started_at_utc, ended_at_utc) VALUES ("
                            "CAST(:run_id AS uuid), 'U_MIGRATION', 'manual', :status, '2026-08', "
                            "'query', now(), now())"
                        ),
                        {"run_id": run_id, "status": status},
                    )
                connection.execute(
                    text(
                        "INSERT INTO raw_artifact ("
                        "raw_artifact_id, ingestion_run_id, account_id, period_key, flex_query_id, "
                        "payload_sha256, source_payload) VALUES ("
                        "CAST(:artifact_id AS uuid), CAST(:owner_run_id AS uuid), 'U_MIGRATION', "
                        "'2026-08', 'query', 'sha', CAST('payload' AS bytea))"
                    ),
                    {"artifact_id": artifact_id, "owner_run_id": owner_run_id},
                )
                assert connection.execute(
                    text(
                        "SELECT completed_ingestion_run_id FROM raw_artifact "
                        "WHERE raw_artifact_id = CAST(:artifact_id AS uuid)"
                    ),
                    {"artifact_id": artifact_id},
                ).scalar_one() is None
                connection.execute(
                    text(
                        "UPDATE raw_artifact SET completed_ingestion_run_id = CAST(:run_id AS uuid) "
                        "WHERE raw_artifact_id = CAST(:artifact_id AS uuid)"
                    ),
                    {"artifact_id": artifact_id, "run_id": completed_run_id},
                )

            with verification_engine.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT completed_ingestion_run_id FROM raw_artifact "
                        "WHERE raw_artifact_id = CAST(:artifact_id AS uuid)"
                    ),
                    {"artifact_id": artifact_id},
                ).scalar_one() == uuid.UUID(completed_run_id)

            with pytest.raises(SQLAlchemyError):
                with verification_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE raw_artifact SET completed_ingestion_run_id = CAST(:run_id AS uuid) "
                            "WHERE raw_artifact_id = CAST(:artifact_id AS uuid)"
                        ),
                        {"artifact_id": artifact_id, "run_id": str(uuid.uuid4())},
                    )
        finally:
            verification_engine.dispose()

        command.downgrade(alembic_config, "20260821_04")
        downgraded_engine = create_engine(temp_database_url)
        try:
            assert "completed_ingestion_run_id" not in {
                column["name"] for column in inspect(downgraded_engine).get_columns("raw_artifact")
            }
        finally:
            downgraded_engine.dispose()
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _migration_drop_database(admin_url=admin_url, database_name=temp_database_name)


def test_migration_06_backfills_calculation_provisional() -> None:
    """Preserve existing final/provisional snapshot meaning during migration."""

    base_url = _migration_resolve_reachable_base_url()
    temp_database_name = f"test_snapshot_provisional_{uuid.uuid4().hex[:10]}"
    admin_url = _migration_build_database_url(base_url, "postgres")
    temp_database_url = _migration_build_database_url(base_url, temp_database_name)
    _migration_create_database(admin_url=admin_url, database_name=temp_database_name)

    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = temp_database_url
    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "20260821_05")
        pre_upgrade_engine = create_engine(temp_database_url)
        try:
            run_id = uuid.uuid4()
            instrument_ids = [uuid.uuid4(), uuid.uuid4()]
            with pre_upgrade_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ingestion_run (ingestion_run_id, account_id, run_type, status, period_key, "
                        "flex_query_id, started_at_utc, ended_at_utc) VALUES "
                        "(:run_id, 'U_MIGRATION', 'manual', 'success', '2026-08-21', 'query', now(), now())"
                    ),
                    {"run_id": run_id},
                )
                for index, (instrument_id, provisional) in enumerate(
                    zip(instrument_ids, (False, True), strict=True)
                ):
                    connection.execute(
                        text(
                            "INSERT INTO instrument (instrument_id, account_id, conid, symbol, asset_category, currency) "
                            "VALUES (:instrument_id, 'U_MIGRATION', :conid, :symbol, 'STK', 'USD')"
                        ),
                        {"instrument_id": instrument_id, "conid": str(index), "symbol": f"T{index}"},
                    )
                    connection.execute(
                        text(
                            "INSERT INTO pnl_snapshot_daily (account_id, report_date_local, instrument_id, "
                            "position_qty, cost_basis, realized_pnl, unrealized_pnl, total_pnl, fees, "
                            "withholding_tax, currency, provisional, ingestion_run_id) VALUES "
                            "('U_MIGRATION', '2026-08-21', :instrument_id, 1, 1, 0, 0, 0, 0, 0, 'USD', "
                            ":provisional, :run_id)"
                        ),
                        {"instrument_id": instrument_id, "provisional": provisional, "run_id": run_id},
                    )
        finally:
            pre_upgrade_engine.dispose()

        command.upgrade(alembic_config, "head")
        verification_engine = create_engine(temp_database_url)
        try:
            with verification_engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT provisional, calculation_provisional FROM pnl_snapshot_daily "
                        "ORDER BY provisional"
                    )
                ).all()
            assert rows == [(False, False), (True, True)]
        finally:
            verification_engine.dispose()

        command.downgrade(alembic_config, "20260821_05")
        downgraded_engine = create_engine(temp_database_url)
        try:
            assert "calculation_provisional" not in {
                column["name"] for column in inspect(downgraded_engine).get_columns("pnl_snapshot_daily")
            }
        finally:
            downgraded_engine.dispose()
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _migration_drop_database(admin_url=admin_url, database_name=temp_database_name)


def test_reconciliation_normalizes_raw_values_and_commission_currency() -> None:
    """Normalize Flex numerics and convert each fee using its actual currency."""

    base_url = _migration_resolve_reachable_base_url()
    temp_database_name = f"test_reconciliation_fx_{uuid.uuid4().hex[:10]}"
    admin_url = _migration_build_database_url(base_url, "postgres")
    temp_database_url = _migration_build_database_url(base_url, temp_database_name)
    _migration_create_database(admin_url=admin_url, database_name=temp_database_name)

    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = temp_database_url
    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")
        engine = create_engine(temp_database_url)
        try:
            run_id = uuid.uuid4()
            instrument_id = uuid.uuid4()
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ingestion_run (ingestion_run_id, account_id, run_type, status, period_key, "
                        "flex_query_id, started_at_utc, ended_at_utc) VALUES "
                        "(:run_id, 'U_RECON', 'scheduled', 'success', '2026-08-21', 'query', now(), now())"
                    ),
                    {"run_id": run_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO instrument (instrument_id, account_id, conid, symbol, asset_category, currency) "
                        "VALUES (:instrument_id, 'U_RECON', '123', 'TEST', 'STK', 'EUR')"
                    ),
                    {"instrument_id": instrument_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO pnl_snapshot_daily (account_id, report_date_local, instrument_id, position_qty, "
                        "cost_basis, realized_pnl, unrealized_pnl, total_pnl, fees, withholding_tax, currency, "
                        "calculation_provisional, provisional, ingestion_run_id) VALUES "
                        "('U_RECON', '2026-08-21', :instrument_id, 1234.5, 0, 0, 0, 0, 0, 0, 'USD', false, false, :run_id)"
                    ),
                    {"instrument_id": instrument_id, "run_id": run_id},
                )
                raw_rows = (
                    (
                        "OpenPositions",
                        "open",
                        {"conid": "123", "currency": "EUR", "position": " 1,234.50 ",
                         "fifoPnlUnrealized": " -- ", "fxRateToBase": " 1.20 "},
                    ),
                    (
                        "Trades",
                        "trade",
                        {"conid": "123", "currency": "EUR", "fifoPnlRealized": " 1,000.25 ",
                         "fxRateToBase": " 1.20 ", "ibCommission": " -2.50 ",
                         "ibCommissionCurrency": "GBP", "fees": " -1.00 "},
                    ),
                    (
                        "CashTransactions",
                        "cash-eur",
                        {"conid": "123", "currency": "EUR", "withholdingTax": " 10.00 ",
                         "fxRateToBase": " 1.20 "},
                    ),
                    (
                        "CashTransactions",
                        "cash-jpy",
                        {"conid": "123", "currency": "JPY", "withholdingTax": " 5.00 ",
                         "fxRateToBase": " N/A "},
                    ),
                    (
                        "ConversionRates",
                        "gbp-usd",
                        {"fromCurrency": "GBP", "toCurrency": "USD", "rate": " 1.30 ",
                         "reportDate": "2026-08-21"},
                    ),
                )
                for section_name, source_row_ref, payload in raw_rows:
                    connection.execute(
                        text(
                            "INSERT INTO raw_record (ingestion_run_id, account_id, period_key, flex_query_id, "
                            "payload_sha256, report_date_local, section_name, source_row_ref, source_payload) VALUES "
                            "(:run_id, 'U_RECON', '2026-08-21', 'query', :payload_sha, '2026-08-21', "
                            ":section_name, :source_row_ref, CAST(:payload AS jsonb))"
                        ),
                        {"run_id": run_id, "payload_sha": source_row_ref, "section_name": section_name,
                         "source_row_ref": source_row_ref, "payload": json.dumps(payload)},
                    )

            rows = SQLAlchemyPortfolioService(engine=engine).db_reconciliation_sources(
                account_id="U_RECON",
                report_date_from=None,
                report_date_to=None,
                instrument_id=None,
            )
            assert len(rows) == 1
            assert Decimal(rows[0].broker_position_qty or "") == Decimal("1234.50")
            assert Decimal(rows[0].broker_realized_pnl or "") == Decimal("1200.300")
            assert rows[0].broker_unrealized_pnl is None
            assert Decimal(rows[0].broker_fees or "") == Decimal("-4.45")
            assert rows[0].broker_withholding_tax is None
        finally:
            engine.dispose()
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _migration_drop_database(admin_url=admin_url, database_name=temp_database_name)


def test_migrations_include_raw_artifact_dedupe_contract() -> None:
    """Require dedicated raw artifact table with unique dedupe identity key.

    Returns:
        None: Assertions validate migration baseline contract.

    Raises:
        AssertionError: Raised when raw artifact table or unique key is missing.
    """

    base_url = _migration_resolve_reachable_base_url()
    temp_database_name = f"test_raw_artifact_{uuid.uuid4().hex[:10]}"
    admin_url = _migration_build_database_url(base_url, "postgres")
    temp_database_url = _migration_build_database_url(base_url, temp_database_name)

    _migration_create_database(admin_url=admin_url, database_name=temp_database_name)

    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = temp_database_url

    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")

        verification_engine = create_engine(temp_database_url)
        try:
            inspector = inspect(verification_engine)
            table_names = set(inspector.get_table_names())
            assert "raw_artifact" in table_names

            unique_constraints = inspector.get_unique_constraints("raw_artifact")
            unique_map = {constraint["name"]: tuple(constraint["column_names"]) for constraint in unique_constraints}
            assert "uq_raw_artifact_account_period_query_sha256" in unique_map
            assert unique_map["uq_raw_artifact_account_period_query_sha256"] == (
                "account_id",
                "period_key",
                "flex_query_id",
                "payload_sha256",
            )
        finally:
            verification_engine.dispose()
    finally:
        if previous_database_url is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _migration_drop_database(admin_url=admin_url, database_name=temp_database_name)


def test_migrations_link_raw_record_to_raw_artifact() -> None:
    """Require raw row provenance to include explicit raw artifact foreign key.

    Returns:
        None: Assertions validate raw provenance schema contract.

    Raises:
        AssertionError: Raised when raw_record linkage to raw_artifact is missing.
    """

    base_url = _migration_resolve_reachable_base_url()
    temp_database_name = f"test_raw_record_link_{uuid.uuid4().hex[:10]}"
    admin_url = _migration_build_database_url(base_url, "postgres")
    temp_database_url = _migration_build_database_url(base_url, temp_database_name)

    _migration_create_database(admin_url=admin_url, database_name=temp_database_name)

    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = temp_database_url

    try:
        alembic_config = Config("alembic.ini")
        command.upgrade(alembic_config, "head")

        verification_engine = create_engine(temp_database_url)
        try:
            inspector = inspect(verification_engine)
            raw_record_columns = {column["name"] for column in inspector.get_columns("raw_record")}
            assert "raw_artifact_id" in raw_record_columns

            foreign_keys = inspector.get_foreign_keys("raw_record")
            raw_artifact_links = [
                key
                for key in foreign_keys
                if key.get("referred_table") == "raw_artifact"
                and key.get("constrained_columns") == ["raw_artifact_id"]
            ]
            assert len(raw_artifact_links) == 1
        finally:
            verification_engine.dispose()
    finally:
        if previous_database_url is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _migration_drop_database(admin_url=admin_url, database_name=temp_database_name)
