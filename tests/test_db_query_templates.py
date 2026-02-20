"""Regression tests for fixed SQL template selection in db-layer query paths."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.db.canonical_persistence import SQLAlchemyCanonicalPersistenceService
from app.db.ingestion_run import SQLAlchemyIngestionRunService
from app.db.ledger_snapshot import SQLAlchemyLedgerSnapshotService


class _MappingResultStub:
    """Stub mapping result wrapper for SQLAlchemy-like query responses."""

    def __init__(self, rows: list[dict]):
        """Initialize mapping result rows.

        Args:
            rows: Row mappings returned by a query.

        Returns:
            None: Initializer does not return values.

        Raises:
            ValueError: This stub does not raise value errors.
        """

        self._rows = rows

    def mappings(self) -> _MappingResultStub:
        """Return self to emulate SQLAlchemy mappings chain.

        Returns:
            _MappingResultStub: This object.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return self

    def all(self) -> list[dict]:
        """Return all row mappings.

        Returns:
            list[dict]: Query rows.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return self._rows


class _ConnectionStub:
    """Connection stub capturing executed SQL and parameters."""

    def __init__(self, rows: list[dict]):
        """Initialize connection capture state.

        Args:
            rows: Query rows returned by execute().

        Returns:
            None: Initializer does not return values.

        Raises:
            ValueError: This stub does not raise value errors.
        """

        self._rows = rows
        self.executed_queries: list[str] = []
        self.executed_parameters: list[dict] = []

    def __enter__(self) -> _ConnectionStub:
        """Enter context manager.

        Returns:
            _ConnectionStub: This object.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        """Exit context manager.

        Args:
            exc_type: Exception type.
            exc: Exception value.
            traceback: Exception traceback.

        Returns:
            bool: False to propagate exceptions.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        _ = (exc_type, exc, traceback)
        return False

    def execute(self, statement, parameters: dict):
        """Capture execute input and return deterministic row result.

        Args:
            statement: SQLAlchemy text clause or raw string.
            parameters: Bound query parameters.

        Returns:
            _MappingResultStub: Query result stub.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        statement_text = getattr(statement, "text", str(statement))
        self.executed_queries.append(statement_text)
        self.executed_parameters.append(parameters)
        return _MappingResultStub(rows=self._rows)


class _EngineStub:
    """Engine stub that returns a predefined connection object."""

    def __init__(self, connection: _ConnectionStub):
        """Initialize engine with a deterministic connection stub.

        Args:
            connection: Connection stub instance.

        Returns:
            None: Initializer does not return values.

        Raises:
            ValueError: This stub does not raise value errors.
        """

        self._connection = connection

    def connect(self) -> _ConnectionStub:
        """Return connection stub.

        Returns:
            _ConnectionStub: Deterministic connection object.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return self._connection

    def begin(self) -> _ConnectionStub:
        """Return connection stub for begin-context compatibility.

        Returns:
            _ConnectionStub: Deterministic connection object.

        Raises:
            RuntimeError: This stub does not raise runtime errors.
        """

        return self._connection


def _build_raw_record_row() -> dict:
    """Build one raw-record row mapping for canonical list queries.

    Returns:
        dict: Mapping row with required columns.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    return {
        "raw_record_id": uuid4(),
        "ingestion_run_id": uuid4(),
        "account_id": "U_TEST",
        "period_key": "2026-02-20",
        "flex_query_id": "query",
        "report_date_local": None,
        "section_name": "Trades",
        "source_row_ref": "Trades:Trade:transactionID=1",
        "source_payload": {"symbol": "AAPL"},
    }


def _build_ingestion_run_row() -> dict:
    """Build one ingestion-run row mapping for list query tests.

    Returns:
        dict: Mapping row with required columns.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    now_utc = datetime.now(timezone.utc)
    return {
        "ingestion_run_id": uuid4(),
        "account_id": "U_TEST",
        "run_type": "manual",
        "status": "success",
        "period_key": "2026-02-20",
        "flex_query_id": "query",
        "report_date_local": None,
        "started_at_utc": now_utc,
        "ended_at_utc": now_utc,
        "duration_ms": 100,
        "error_code": None,
        "error_message": None,
        "diagnostics": [],
        "created_at_utc": now_utc,
    }


def test_db_canonical_raw_record_list_for_period_uses_fixed_query_template() -> None:
    """Select canonical raw rows with fixed account/period/query template.

    Returns:
        None: Assertions validate SQL template selection.

    Raises:
        AssertionError: Raised when selected SQL diverges from policy.
    """

    connection = _ConnectionStub(rows=[_build_raw_record_row()])
    service = SQLAlchemyCanonicalPersistenceService(engine=_EngineStub(connection=connection))

    service.db_raw_record_list_for_period(account_id="U_TEST", period_key="2026-02-20", flex_query_id="query")

    executed_query = connection.executed_queries[0]
    assert "WHERE account_id = :account_id AND period_key = :period_key AND flex_query_id = :flex_query_id" in executed_query
    assert "ORDER BY created_at_utc ASC, raw_record_id ASC" in executed_query


def test_db_canonical_raw_record_list_for_run_uses_fixed_query_template() -> None:
    """Select canonical raw rows with fixed ingestion-run query template.

    Returns:
        None: Assertions validate SQL template selection.

    Raises:
        AssertionError: Raised when selected SQL diverges from policy.
    """

    connection = _ConnectionStub(rows=[_build_raw_record_row()])
    service = SQLAlchemyCanonicalPersistenceService(engine=_EngineStub(connection=connection))

    service.db_raw_record_list_for_run(ingestion_run_id=uuid4())

    executed_query = connection.executed_queries[0]
    assert "WHERE ingestion_run_id = CAST(:ingestion_run_id AS uuid)" in executed_query
    assert "ORDER BY created_at_utc ASC, raw_record_id ASC" in executed_query


def test_db_ingestion_run_list_uses_fixed_sort_template() -> None:
    """List ingestion runs with fixed ORDER BY template selected by validated mode.

    Returns:
        None: Assertions validate SQL template selection.

    Raises:
        AssertionError: Raised when selected SQL diverges from policy.
    """

    connection = _ConnectionStub(rows=[_build_ingestion_run_row()])
    service = SQLAlchemyIngestionRunService(engine=_EngineStub(connection=connection))

    service.db_ingestion_run_list(limit=10, offset=0, sort_by="status", sort_dir="asc")

    executed_query = connection.executed_queries[0]
    assert "ORDER BY status asc, ingestion_run_id asc" in executed_query
    assert connection.executed_parameters[0] == {"limit": 10, "offset": 0}


def test_db_ingestion_run_list_rejects_unsupported_sort_field() -> None:
    """Reject invalid sort field before query execution.

    Returns:
        None: Assertions validate deterministic contract enforcement.

    Raises:
        AssertionError: Raised when validation behavior diverges.
    """

    connection = _ConnectionStub(rows=[_build_ingestion_run_row()])
    service = SQLAlchemyIngestionRunService(engine=_EngineStub(connection=connection))

    try:
        service.db_ingestion_run_list(limit=10, offset=0, sort_by="created_at_utc", sort_dir="asc")
        assert False, "Expected ValueError for unsupported sort field"
    except ValueError as error:
        assert str(error) == "unsupported sort_by=created_at_utc"
    assert len(connection.executed_queries) == 0


def test_db_ledger_trade_fill_list_uses_typed_nullable_date_predicate() -> None:
    """Use typed nullable-date predicate in ledger trade-fill query template.

    Returns:
        None: Assertions validate SQL template text.

    Raises:
        AssertionError: Raised when typed nullable-date casting is missing.
    """

    connection = _ConnectionStub(rows=[])
    service = SQLAlchemyLedgerSnapshotService(engine=_EngineStub(connection=connection))

    service.db_ledger_trade_fill_list_for_account(account_id="U_TEST", through_report_date_local="2026-02-20")

    executed_query = connection.executed_queries[0]
    assert "CAST(:through_report_date_local AS date) IS NULL" in executed_query
    assert "report_date_local <= CAST(:through_report_date_local AS date)" in executed_query


def test_db_snapshot_list_uses_typed_nullable_date_predicates() -> None:
    """Use typed nullable-date predicates in snapshot list query templates.

    Returns:
        None: Assertions validate SQL template text.

    Raises:
        AssertionError: Raised when typed nullable-date casting is missing.
    """

    connection = _ConnectionStub(rows=[])
    service = SQLAlchemyLedgerSnapshotService(engine=_EngineStub(connection=connection))

    service.db_pnl_snapshot_daily_list(
        account_id="U_TEST",
        limit=10,
        offset=0,
        sort_by="report_date_local",
        sort_dir="desc",
        report_date_from="2026-02-01",
        report_date_to="2026-02-28",
    )

    executed_query = connection.executed_queries[0]
    assert "CAST(:report_date_from AS date) IS NULL" in executed_query
    assert "CAST(:report_date_to AS date) IS NULL" in executed_query
