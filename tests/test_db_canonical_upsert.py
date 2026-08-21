"""Regression tests for Task 5 canonical DB UPSERT behavior."""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from types import TracebackType
from typing import Literal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from alembic import command
from alembic.config import Config

from app.db import db_create_engine
from app.db.canonical_persistence import (
    CanonicalCashflowUpsertRequest,
    CanonicalInstrumentUpsertRequest,
    CanonicalTradeFillUpsertRequest,
    SQLAlchemyCanonicalPersistenceService,
)
from app.config import config_load_settings
from app.db.raw_persistence import SQLAlchemyRawPersistenceService
from app.db.interfaces import (
    RawArtifactPersistRequest,
    RawArtifactReference,
    RawArtifactReplayCandidate,
    RawRecordPersistRequest,
)


class _InstrumentBatchResultSpy:
    """Minimal SQLAlchemy result chain for instrument batch execution tests."""

    def mappings(self) -> _InstrumentBatchResultSpy:
        """Return this result as a mapping result."""

        return self

    def all(self) -> list[dict[str, object]]:
        """Return no rows for the execution-count test."""

        return []


class _InstrumentBatchConnectionSpy:
    """Capture instrument batch statement executions."""

    def __init__(self, error: SQLAlchemyError | None = None) -> None:
        """Initialize statement capture and an optional database error."""

        self.executions: list[tuple[object, dict[str, object]]] = []
        self._error = error

    def __enter__(self) -> _InstrumentBatchConnectionSpy:
        """Enter the transaction context."""

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        """Propagate transaction errors."""

        _ = (exc_type, exc_value, traceback)
        return False

    def execute(
        self,
        statement: object,
        parameters: dict[str, object],
    ) -> _InstrumentBatchResultSpy:
        """Capture one statement execution or raise the configured error."""

        self.executions.append((statement, parameters))
        if self._error is not None:
            raise self._error
        return _InstrumentBatchResultSpy()


class _InstrumentBatchEngineSpy:
    """Capture transaction creation for instrument batch execution tests."""

    def __init__(self, connection: _InstrumentBatchConnectionSpy) -> None:
        """Initialize with the connection returned for each transaction."""

        self.begin_calls = 0
        self._connection = connection

    def begin(self) -> _InstrumentBatchConnectionSpy:
        """Record and return the batch transaction connection."""

        self.begin_calls += 1
        return self._connection


class _ReplayReadResult:
    """Expose configured mapping rows through the SQLAlchemy result chain."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _ReplayReadResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _ReplayReadConnection:
    """Capture replay discovery SQL without requiring a database connection."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows
        self.executed_queries: list[str] = []
        self.executed_parameters: list[object] = []

    def __enter__(self) -> _ReplayReadConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        _ = (exc_type, exc_value, traceback)
        return False

    def execute(self, statement: object, parameters: object) -> _ReplayReadResult:
        self.executed_queries.append(str(statement))
        self.executed_parameters.append(parameters)
        return _ReplayReadResult(self._rows)


class _ReplayReadEngine:
    """Return the configured replay-read connection."""

    def __init__(self, connection: _ReplayReadConnection) -> None:
        self._connection = connection

    def connect(self) -> _ReplayReadConnection:
        return self._connection


def _instrument_upsert_request(
    account_id: str,
    conid: str,
    symbol: str,
    *,
    local_symbol: str | None = None,
    isin: str | None = None,
    cusip: str | None = None,
    figi: str | None = None,
    description: str | None = None,
) -> CanonicalInstrumentUpsertRequest:
    """Build one canonical instrument request with fixed required metadata."""

    return CanonicalInstrumentUpsertRequest(
        account_id=account_id,
        conid=conid,
        symbol=symbol,
        local_symbol=local_symbol,
        isin=isin,
        cusip=cusip,
        figi=figi,
        asset_category="STK",
        currency="USD",
        description=description,
    )


def test_replay_candidate_query_returns_successful_artifacts_in_source_order() -> None:
    """Expose successful replay artifacts in deterministic source order."""

    older_artifact_id = uuid.uuid4()
    older_owner_id = uuid.uuid4()
    newer_artifact_id = uuid.uuid4()
    newer_owner_id = uuid.uuid4()
    connection = _ReplayReadConnection([
        {
            "raw_artifact_id": older_artifact_id,
            "ingestion_run_id": older_owner_id,
            "report_date_local": date(2026, 2, 19),
            "created_at_utc": datetime(2026, 2, 20, tzinfo=timezone.utc),
            "open_positions_present": True,
        },
        {
            "raw_artifact_id": newer_artifact_id,
            "ingestion_run_id": newer_owner_id,
            "report_date_local": date(2026, 8, 20),
            "created_at_utc": datetime(2026, 8, 21, tzinfo=timezone.utc),
            "open_positions_present": True,
        },
    ])
    repository = SQLAlchemyCanonicalPersistenceService(_ReplayReadEngine(connection))

    candidates = repository.db_raw_artifact_replay_candidate_list(
        account_id="U1",
        period_key="2026-02-20",
        flex_query_id="query",
    )

    assert candidates == [
        RawArtifactReplayCandidate(
            raw_artifact_id=older_artifact_id,
            ingestion_run_id=older_owner_id,
            report_date_local=date(2026, 2, 19),
            created_at_utc=datetime(2026, 2, 20, tzinfo=timezone.utc),
            open_positions_present=True,
        ),
        RawArtifactReplayCandidate(
            raw_artifact_id=newer_artifact_id,
            ingestion_run_id=newer_owner_id,
            report_date_local=date(2026, 8, 20),
            created_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
            open_positions_present=True,
        ),
    ]
    query = connection.executed_queries[0]
    assert "JOIN ingestion_run owner ON owner.ingestion_run_id = artifact.ingestion_run_id" in query
    assert "LEFT JOIN ingestion_run completion ON completion.ingestion_run_id = artifact.completed_ingestion_run_id" in query
    assert "artifact.completed_ingestion_run_id IS NOT NULL AND completion.status = 'success'" in query
    assert "artifact.completed_ingestion_run_id IS NULL AND owner.status = 'success'" in query
    assert "position_row.raw_artifact_id = artifact.raw_artifact_id" in query
    assert "position_row.section_name = 'OpenPositions'" in query
    assert "ORDER BY artifact.report_date_local ASC, artifact.created_at_utc ASC, artifact.raw_artifact_id ASC" in query
    assert connection.executed_parameters == [{
        "account_id": "U1",
        "period_key": "2026-02-20",
        "flex_query_id": "query",
    }]


def test_batch_instrument_upsert_preserves_optional_metadata_when_later_request_omits_it() -> None:
    """Keep existing optional instrument metadata when a later source row omits it."""

    engine = db_create_engine(config_load_settings().database_url)
    account_id = f"U_BATCH_METADATA_{uuid.uuid4().hex[:8]}"
    repository = SQLAlchemyCanonicalPersistenceService(engine)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        engine.dispose()
        pytest.skip("PostgreSQL is not reachable for batch instrument integration test")

    try:
        initial_record = repository.db_canonical_instrument_upsert_many([
            _instrument_upsert_request(
                account_id,
                "100",
                "AAA",
                local_symbol="AAA.LOCAL",
                isin="US0000000001",
                cusip="000000001",
                figi="BBG000000001",
                description="Initial description",
            )
        ])[0]
        later_record = repository.db_canonical_instrument_upsert_many([
            _instrument_upsert_request(
                account_id,
                "100",
                "AAA-UPDATED",
                local_symbol=" ",
                description=" ",
            )
        ])[0]

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT symbol, local_symbol, isin, cusip, figi, description "
                    "FROM instrument WHERE account_id = :account_id AND conid = '100'"
                ),
                {"account_id": account_id},
            ).mappings().one()

        assert later_record.instrument_id == initial_record.instrument_id
        assert dict(row) == {
            "symbol": "AAA-UPDATED",
            "local_symbol": "AAA.LOCAL",
            "isin": "US0000000001",
            "cusip": "000000001",
            "figi": "BBG000000001",
            "description": "Initial description",
        }
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM instrument WHERE account_id=:account_id"), {"account_id": account_id})
        engine.dispose()


def test_batch_instrument_upsert_executes_one_statement_for_many_requests() -> None:
    """Use one transaction and statement for every non-empty instrument batch."""

    connection = _InstrumentBatchConnectionSpy()
    engine = _InstrumentBatchEngineSpy(connection)
    repository = SQLAlchemyCanonicalPersistenceService(engine)

    records = repository.db_canonical_instrument_upsert_many([
        _instrument_upsert_request("U_SPY", "100", "AAA"),
        _instrument_upsert_request("U_SPY", "200", "BBB"),
    ])

    assert records == []
    assert engine.begin_calls == 1
    assert len(connection.executions) == 1


def test_batch_instrument_upsert_skips_transaction_for_empty_requests() -> None:
    """Avoid a database round trip when there are no instruments to persist."""

    connection = _InstrumentBatchConnectionSpy()
    engine = _InstrumentBatchEngineSpy(connection)
    repository = SQLAlchemyCanonicalPersistenceService(engine)

    assert repository.db_canonical_instrument_upsert_many([]) == []
    assert engine.begin_calls == 0
    assert connection.executions == []


def test_batch_instrument_upsert_rejects_none_request_list() -> None:
    """Raise the documented validation error when the request list is missing."""

    repository = SQLAlchemyCanonicalPersistenceService(_InstrumentBatchEngineSpy(_InstrumentBatchConnectionSpy()))

    with pytest.raises(ValueError, match="^requests must not be None$"):
        repository.db_canonical_instrument_upsert_many(None)


def test_single_instrument_upsert_preserves_established_database_error_message() -> None:
    """Keep the single-instrument persistence error contract for compatibility callers."""

    repository = SQLAlchemyCanonicalPersistenceService(
        _InstrumentBatchEngineSpy(_InstrumentBatchConnectionSpy(SQLAlchemyError("database unavailable")))
    )

    with pytest.raises(RuntimeError, match="^canonical instrument upsert failed$"):
        repository.db_canonical_instrument_upsert(_instrument_upsert_request("U_SPY", "100", "AAA"))


def test_batch_instrument_upsert_returns_records_by_conid() -> None:
    """Persist every batch instrument and return its canonical identity."""

    engine = db_create_engine(config_load_settings().database_url)
    account_id = f"U_BATCH_{uuid.uuid4().hex[:8]}"
    repository = SQLAlchemyCanonicalPersistenceService(engine)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        engine.dispose()
        pytest.skip("PostgreSQL is not reachable for batch instrument integration test")

    def request(conid: str, symbol: str) -> CanonicalInstrumentUpsertRequest:
        return CanonicalInstrumentUpsertRequest(
            account_id=account_id,
            conid=conid,
            symbol=symbol,
            local_symbol=symbol,
            isin=None,
            cusip=None,
            figi=None,
            asset_category="STK",
            currency="USD",
            description=None,
        )

    try:
        records = repository.db_canonical_instrument_upsert_many([
            request("100", "AAA"), request("200", "BBB")
        ])
        assert {record.conid for record in records} == {"100", "200"}
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM instrument WHERE account_id=:account_id"), {"account_id": account_id})
        engine.dispose()


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
    return str(parsed_url.set(database=database_name).render_as_string(hide_password=False))


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
        probe_engine = create_engine(candidate_url, connect_args={"connect_timeout": 1})
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


def test_artifact_rows_and_completion_marker_follow_retry_lineage() -> None:
    """Read rows by artifact identity and persist the run that completed processing."""

    base_url = _upsert_resolve_reachable_base_url()
    database_name = f"test_artifact_lineage_{uuid.uuid4().hex[:10]}"
    admin_url = _upsert_build_database_url(base_url, "postgres")
    database_url = _upsert_build_database_url(base_url, database_name)
    previous_database_url = os.environ.get("DATABASE_URL")
    _upsert_create_database(admin_url, database_name)
    os.environ["DATABASE_URL"] = database_url
    engine = None
    try:
        command.upgrade(Config("alembic.ini"), "head")
        engine = db_create_engine(database_url)
        owner_run_id = uuid.uuid4()
        retry_run_id = uuid.uuid4()
        with engine.begin() as connection:
            for run_id, status in (
                (owner_run_id, "failed"),
                (retry_run_id, "started"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO ingestion_run ("
                        "ingestion_run_id, account_id, run_type, status, period_key, flex_query_id, "
                        "started_at_utc, ended_at_utc) VALUES ("
                        ":run_id, 'U_LINEAGE', 'manual', :status, '2026-08', 'query', now(), "
                        "CASE WHEN :status = 'started' THEN NULL ELSE now() END)"
                    ),
                        {"run_id": run_id, "status": status},
                )

        raw_repository = SQLAlchemyRawPersistenceService(engine)
        canonical_repository = SQLAlchemyCanonicalPersistenceService(engine)
        artifact_result = raw_repository.db_raw_artifact_upsert(
            RawArtifactPersistRequest(
                ingestion_run_id=owner_run_id,
                reference=RawArtifactReference(
                    account_id="U_LINEAGE",
                    period_key="2026-08",
                    flex_query_id="query",
                    payload_sha256="artifact-lineage-sha",
                    report_date_local=None,
                ),
                source_payload=b"artifact-lineage",
            )
        )
        assert artifact_result.artifact.completed_ingestion_run_id is None

        raw_repository.db_raw_record_insert_many(
            [
                RawRecordPersistRequest(
                    ingestion_run_id=retry_run_id,
                    raw_artifact_id=artifact_result.artifact.raw_artifact_id,
                    artifact_reference=artifact_result.artifact.reference,
                    report_date_local=None,
                    section_name="Trades",
                    source_row_ref="Trades:Trade:transactionID=1",
                    source_payload={"transactionID": "1"},
                ),
                RawRecordPersistRequest(
                    ingestion_run_id=retry_run_id,
                    raw_artifact_id=artifact_result.artifact.raw_artifact_id,
                    artifact_reference=artifact_result.artifact.reference,
                    report_date_local=None,
                    section_name="OpenPositions",
                    source_row_ref="OpenPositions:OpenPosition:conid=100",
                    source_payload={"conid": "100"},
                ),
            ]
        )
        artifact_rows = canonical_repository.db_raw_record_list_for_artifact(
            artifact_result.artifact.raw_artifact_id
        )
        assert {row.section_name for row in artifact_rows} == {"Trades", "OpenPositions"}
        assert [row.raw_record_id for row in artifact_rows] == sorted(
            row.raw_record_id for row in artifact_rows
        )
        assert {row.ingestion_run_id for row in artifact_rows} == {retry_run_id}

        raw_repository.db_raw_artifact_mark_completed(
            artifact_result.artifact.raw_artifact_id,
            retry_run_id,
        )
        duplicate_result = raw_repository.db_raw_artifact_upsert(
            RawArtifactPersistRequest(
                ingestion_run_id=uuid.uuid4(),
                reference=artifact_result.artifact.reference,
                source_payload=b"artifact-lineage",
            )
        )
        assert duplicate_result.deduplicated is True
        assert duplicate_result.artifact.ingestion_run_id == owner_run_id
        assert duplicate_result.artifact.completed_ingestion_run_id == retry_run_id
    finally:
        if engine is not None:
            engine.dispose()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _upsert_drop_database(admin_url, database_name)


def _upsert_insert_delta_run_and_artifact(
    connection: Connection,
    *,
    ingestion_run_id: str,
    raw_artifact_id: str,
    account_id: str,
    flex_query_id: str,
    created_at_utc: str,
    run_status: str = "success",
    completed: bool = True,
) -> None:
    """Insert one delta-read ingestion run and its raw artifact fixture."""

    payload_sha256 = f"sha-{raw_artifact_id}"
    connection.execute(
        text(
            "INSERT INTO ingestion_run (ingestion_run_id, account_id, run_type, status, "
            "period_key, flex_query_id, started_at_utc, ended_at_utc) VALUES "
            "(CAST(:run_id AS uuid), :account_id, 'manual', :run_status, '2026-08', :flex_query_id, "
            "CAST(:created AS timestamptz), CAST(:created AS timestamptz))"
        ),
        {
            "run_id": ingestion_run_id,
            "account_id": account_id,
            "flex_query_id": flex_query_id,
            "run_status": run_status,
            "created": created_at_utc,
        },
    )
    connection.execute(
        text(
            "INSERT INTO raw_artifact (raw_artifact_id, ingestion_run_id, account_id, period_key, "
            "flex_query_id, payload_sha256, report_date_local, source_payload, created_at_utc, "
            "completed_ingestion_run_id) VALUES "
            "(CAST(:artifact_id AS uuid), CAST(:run_id AS uuid), :account_id, '2026-08', :flex_query_id, :sha, "
            "DATE '2026-08-21', CAST(:source_payload AS bytea), CAST(:created AS timestamptz), "
            "CAST(:completed_run_id AS uuid))"
        ),
        {
            "artifact_id": raw_artifact_id,
            "run_id": ingestion_run_id,
            "account_id": account_id,
            "flex_query_id": flex_query_id,
            "sha": payload_sha256,
            "source_payload": payload_sha256,
            "created": created_at_utc,
            "completed_run_id": ingestion_run_id if completed else None,
        },
    )


def _upsert_insert_delta_raw_record(
    connection: Connection,
    *,
    raw_record_id: str,
    raw_artifact_id: str,
    ingestion_run_id: str,
    account_id: str,
    flex_query_id: str,
    source_row_ref: str,
    source_payload: str,
    created_at_utc: str,
) -> None:
    """Insert one raw-row fixture for delta-read tests."""

    connection.execute(
        text(
            "INSERT INTO raw_record (raw_record_id, raw_artifact_id, ingestion_run_id, account_id, "
            "period_key, flex_query_id, payload_sha256, report_date_local, section_name, "
            "source_row_ref, source_payload, created_at_utc) VALUES "
            "(CAST(:raw_record_id AS uuid), CAST(:artifact_id AS uuid), CAST(:run_id AS uuid), :account_id, "
            "'2026-08', :flex_query_id, :sha, DATE '2026-08-21', 'Trades', :source_row_ref, "
            "CAST(:source_payload AS jsonb), CAST(:created AS timestamptz))"
        ),
        {
            "raw_record_id": raw_record_id,
            "artifact_id": raw_artifact_id,
            "run_id": ingestion_run_id,
            "account_id": account_id,
            "flex_query_id": flex_query_id,
            "sha": f"sha-{raw_artifact_id}",
            "source_row_ref": source_row_ref,
            "source_payload": source_payload,
            "created": created_at_utc,
        },
    )


def _upsert_seed_dependencies(engine: Engine) -> tuple[str, str, str, str, str]:
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
                "'Trades', 'Trades:Trade:transactionID=1', CAST(:source_record_payload AS jsonb)"
                ")"
            ),
            {
                "raw_record_id": raw_record_id,
                "raw_artifact_id": raw_artifact_id,
                "ingestion_run_id": ingestion_run_1,
                "account_id": account_id,
                "source_record_payload": '{"x": 1}',
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
                price="111",
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
                    "SELECT ingestion_run_id::text AS ingestion_run_id, commission::text AS commission, "
                    "price::text AS price "
                    "FROM event_trade_fill WHERE account_id = :account_id AND ib_exec_id = :ib_exec_id"
                ),
                {"account_id": account_id, "ib_exec_id": "EXEC-1"},
            ).mappings().one()

        assert row["ingestion_run_id"] == run_id_1
        assert row["commission"] == "2.50000000"
        assert row["price"] == "111.00000000"
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
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL is not reachable for raw persistence integration test")

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


def test_changed_rows_ignore_failed_and_unprocessed_artifact_predecessors() -> None:
    """Compare a later artifact with the latest successfully completed semantic baseline."""

    base_url = _upsert_resolve_reachable_base_url()
    database_name = f"test_completed_baseline_{uuid.uuid4().hex[:10]}"
    admin_url = _upsert_build_database_url(base_url, "postgres")
    database_url = _upsert_build_database_url(base_url, database_name)
    previous_database_url = os.environ.get("DATABASE_URL")
    _upsert_create_database(admin_url, database_name)
    os.environ["DATABASE_URL"] = database_url
    engine = None
    try:
        command.upgrade(Config("alembic.ini"), "head")
        engine = db_create_engine(database_url)
        fixture_ids = [
            (str(uuid.uuid4()), str(uuid.uuid4()))
            for _ in range(4)
        ]
        with engine.begin() as connection:
            for index, ((run_id, artifact_id), payload, status, completed) in enumerate(
                zip(
                    fixture_ids,
                    ('{"price":"10"}', '{"price":"11"}', '{"price":"12"}', '{"price":"12"}'),
                    ("success", "failed", "failed", "started"),
                    (True, False, True, False),
                    strict=True,
                ),
                start=1,
            ):
                _upsert_insert_delta_run_and_artifact(
                    connection,
                    ingestion_run_id=run_id,
                    raw_artifact_id=artifact_id,
                    account_id="U_COMPLETED_BASELINE",
                    flex_query_id="query-baseline",
                    created_at_utc=f"2026-08-21T01:00:0{index}+00:00",
                    run_status=status,
                    completed=completed,
                )
                _upsert_insert_delta_raw_record(
                    connection,
                    raw_record_id=str(uuid.uuid4()),
                    raw_artifact_id=artifact_id,
                    ingestion_run_id=run_id,
                    account_id="U_COMPLETED_BASELINE",
                    flex_query_id="query-baseline",
                    source_row_ref="trade-baseline",
                    source_payload=payload,
                    created_at_utc=f"2026-08-21T01:00:0{index}+00:00",
                )

        rows = SQLAlchemyCanonicalPersistenceService(engine).db_raw_record_list_changed_for_run(
            uuid.UUID(fixture_ids[-1][0])
        )
        assert len(rows) == 1
        assert rows[0].source_payload == {"price": "12"}
    finally:
        if engine is not None:
            engine.dispose()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _upsert_drop_database(admin_url, database_name)


def test_changed_rows_compare_with_immediate_predecessor() -> None:
    """Select rows whose payload differs from their immediate prior version."""

    base_url = _upsert_resolve_reachable_base_url()
    database_name = f"test_changed_rows_{uuid.uuid4().hex[:10]}"
    admin_url = _upsert_build_database_url(base_url, "postgres")
    database_url = _upsert_build_database_url(base_url, database_name)
    previous_database_url = os.environ.get("DATABASE_URL")
    _upsert_create_database(admin_url, database_name)
    os.environ["DATABASE_URL"] = database_url
    engine = None
    try:
        command.upgrade(Config("alembic.ini"), "head")
        engine = db_create_engine(database_url)
        run_ids = [str(uuid.uuid4()) for _ in range(4)]
        artifact_ids = [str(uuid.uuid4()) for _ in range(4)]
        payloads = ['{"price":"10"}', '{"price":"11"}', '{"price":"10"}', '{"price":"10"}']
        with engine.begin() as connection:
            for index, (run_id, artifact_id, payload) in enumerate(
                zip(run_ids, artifact_ids, payloads, strict=True), start=1
            ):
                connection.execute(
                    text(
                        "INSERT INTO ingestion_run (ingestion_run_id, account_id, run_type, status, "
                        "period_key, flex_query_id, started_at_utc, ended_at_utc) VALUES "
                        "(CAST(:run_id AS uuid), 'U1', 'manual', 'success', '2026-08', 'query', "
                        "CAST(:created AS timestamptz), CAST(:created AS timestamptz))"
                    ),
                    {"run_id": run_id, "created": f"2026-08-21T00:00:0{index}+00:00"},
                )
                connection.execute(
                    text(
                        "INSERT INTO raw_artifact (raw_artifact_id, ingestion_run_id, account_id, period_key, "
                        "flex_query_id, payload_sha256, report_date_local, source_payload, created_at_utc, "
                        "completed_ingestion_run_id) VALUES "
                        "(CAST(:artifact_id AS uuid), CAST(:run_id AS uuid), 'U1', '2026-08', 'query', CAST(:sha AS bytea), "
                        "DATE '2026-08-21', CAST(:sha AS bytea), CAST(:created AS timestamptz), CAST(:run_id AS uuid))"
                    ),
                    {
                        "artifact_id": artifact_id,
                        "run_id": run_id,
                        "sha": f"sha-{index}",
                        "created": f"2026-08-21T00:00:0{index}+00:00",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO raw_record (raw_record_id, raw_artifact_id, ingestion_run_id, account_id, "
                        "period_key, flex_query_id, payload_sha256, report_date_local, section_name, "
                        "source_row_ref, source_payload, created_at_utc) VALUES "
                        "(gen_random_uuid(), CAST(:artifact_id AS uuid), CAST(:run_id AS uuid), 'U1', '2026-08', "
                        "'query', :sha, DATE '2026-08-21', 'Trades', 'trade-1', CAST(:payload AS jsonb), "
                        "CAST(:created AS timestamptz))"
                    ),
                    {
                        "artifact_id": artifact_id,
                        "run_id": run_id,
                        "sha": f"sha-{index}",
                        "payload": payload,
                        "created": f"2026-08-21T00:00:0{index}+00:00",
                    },
                )

        repository = SQLAlchemyCanonicalPersistenceService(engine)
        assert len(repository.db_raw_record_list_changed_for_run(uuid.UUID(run_ids[0]))) == 1
        assert len(repository.db_raw_record_list_changed_for_run(uuid.UUID(run_ids[1]))) == 1
        assert len(repository.db_raw_record_list_changed_for_run(uuid.UUID(run_ids[2]))) == 1
        assert repository.db_raw_record_list_changed_for_run(uuid.UUID(run_ids[3])) == []

        multiple_prior_run_id = str(uuid.uuid4())
        multiple_current_run_id = str(uuid.uuid4())
        multiple_prior_artifact_id = str(uuid.uuid4())
        multiple_current_artifact_id = str(uuid.uuid4())
        with engine.begin() as connection:
            _upsert_insert_delta_run_and_artifact(
                connection,
                ingestion_run_id=multiple_prior_run_id,
                raw_artifact_id=multiple_prior_artifact_id,
                account_id="U_MULTIPLE",
                flex_query_id="query-multiple",
                created_at_utc="2026-08-21T00:01:00+00:00",
            )
            _upsert_insert_delta_run_and_artifact(
                connection,
                ingestion_run_id=multiple_current_run_id,
                raw_artifact_id=multiple_current_artifact_id,
                account_id="U_MULTIPLE",
                flex_query_id="query-multiple",
                created_at_utc="2026-08-21T00:02:00+00:00",
            )
            for source_row_ref, source_payload in (("trade-changed", '{"price":"10"}'), ("trade-unchanged", '{"price":"20"}')):
                _upsert_insert_delta_raw_record(
                    connection,
                    raw_record_id=str(uuid.uuid4()),
                    raw_artifact_id=multiple_prior_artifact_id,
                    ingestion_run_id=multiple_prior_run_id,
                    account_id="U_MULTIPLE",
                    flex_query_id="query-multiple",
                    source_row_ref=source_row_ref,
                    source_payload=source_payload,
                    created_at_utc="2026-08-21T00:01:00+00:00",
                )
            _upsert_insert_delta_raw_record(
                connection,
                raw_record_id=str(uuid.uuid4()),
                raw_artifact_id=multiple_current_artifact_id,
                ingestion_run_id=multiple_current_run_id,
                account_id="U_MULTIPLE",
                flex_query_id="query-multiple",
                source_row_ref="trade-changed",
                source_payload='{"price":"11"}',
                created_at_utc="2026-08-21T00:02:00+00:00",
            )
            _upsert_insert_delta_raw_record(
                connection,
                raw_record_id=str(uuid.uuid4()),
                raw_artifact_id=multiple_current_artifact_id,
                ingestion_run_id=multiple_current_run_id,
                account_id="U_MULTIPLE",
                flex_query_id="query-multiple",
                source_row_ref="trade-unchanged",
                source_payload='{"price":"20"}',
                created_at_utc="2026-08-21T00:02:00+00:00",
            )

        multiple_current_rows = repository.db_raw_record_list_changed_for_run(uuid.UUID(multiple_current_run_id))
        assert [row.source_row_ref for row in multiple_current_rows] == ["trade-changed"]

        tie_prior_run_id = str(uuid.uuid4())
        tie_current_run_id = str(uuid.uuid4())
        tie_prior_artifact_id = str(uuid.uuid4())
        tie_current_artifact_id = str(uuid.uuid4())
        with engine.begin() as connection:
            _upsert_insert_delta_run_and_artifact(
                connection,
                ingestion_run_id=tie_prior_run_id,
                raw_artifact_id=tie_prior_artifact_id,
                account_id="U_TIE",
                flex_query_id="query-tie",
                created_at_utc="2026-08-21T00:03:00+00:00",
            )
            _upsert_insert_delta_run_and_artifact(
                connection,
                ingestion_run_id=tie_current_run_id,
                raw_artifact_id=tie_current_artifact_id,
                account_id="U_TIE",
                flex_query_id="query-tie",
                created_at_utc="2026-08-21T00:03:00+00:00",
            )
            _upsert_insert_delta_raw_record(
                connection,
                raw_record_id="00000000-0000-0000-0000-000000000001",
                raw_artifact_id=tie_prior_artifact_id,
                ingestion_run_id=tie_prior_run_id,
                account_id="U_TIE",
                flex_query_id="query-tie",
                source_row_ref="trade-tie",
                source_payload='{"price":"30"}',
                created_at_utc="2026-08-21T00:03:00+00:00",
            )
            _upsert_insert_delta_raw_record(
                connection,
                raw_record_id="00000000-0000-0000-0000-000000000002",
                raw_artifact_id=tie_current_artifact_id,
                ingestion_run_id=tie_current_run_id,
                account_id="U_TIE",
                flex_query_id="query-tie",
                source_row_ref="trade-tie",
                source_payload='{"price":"30"}',
                created_at_utc="2026-08-21T00:03:00+00:00",
            )

        assert repository.db_raw_record_list_changed_for_run(uuid.UUID(tie_current_run_id)) == []

        partition_other_account_run_id = str(uuid.uuid4())
        partition_other_query_run_id = str(uuid.uuid4())
        partition_current_run_id = str(uuid.uuid4())
        partition_other_account_artifact_id = str(uuid.uuid4())
        partition_other_query_artifact_id = str(uuid.uuid4())
        partition_current_artifact_id = str(uuid.uuid4())
        with engine.begin() as connection:
            for run_id, artifact_id, account_id, flex_query_id, created_at_utc in (
                (
                    partition_other_account_run_id,
                    partition_other_account_artifact_id,
                    "U_OTHER",
                    "query-isolated",
                    "2026-08-21T00:04:00+00:00",
                ),
                (
                    partition_other_query_run_id,
                    partition_other_query_artifact_id,
                    "U_PARTITION",
                    "query-other",
                    "2026-08-21T00:05:00+00:00",
                ),
                (
                    partition_current_run_id,
                    partition_current_artifact_id,
                    "U_PARTITION",
                    "query-isolated",
                    "2026-08-21T00:06:00+00:00",
                ),
            ):
                _upsert_insert_delta_run_and_artifact(
                    connection,
                    ingestion_run_id=run_id,
                    raw_artifact_id=artifact_id,
                    account_id=account_id,
                    flex_query_id=flex_query_id,
                    created_at_utc=created_at_utc,
                )
                _upsert_insert_delta_raw_record(
                    connection,
                    raw_record_id=str(uuid.uuid4()),
                    raw_artifact_id=artifact_id,
                    ingestion_run_id=run_id,
                    account_id=account_id,
                    flex_query_id=flex_query_id,
                    source_row_ref="trade-isolated",
                    source_payload='{"price":"40"}',
                    created_at_utc=created_at_utc,
                )

        partition_current_rows = repository.db_raw_record_list_changed_for_run(uuid.UUID(partition_current_run_id))
        assert len(partition_current_rows) == 1
        assert partition_current_rows[0].account_id == "U_PARTITION"
        assert partition_current_rows[0].flex_query_id == "query-isolated"
    finally:
        if engine is not None:
            engine.dispose()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _upsert_drop_database(admin_url, database_name)
