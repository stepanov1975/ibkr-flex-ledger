"""Revision contexts preserve caller-owned transactions and accounting atomicity."""

from typing import Any, NoReturn

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Connection, Engine, text

from app.db.corporate_action_accounting import CorporateActionAccountingRevision
from app.db.corporate_action_evidence import accounting_inputs
from app.db.portfolio_interfaces import CorporateActionManualCaseRecord
from app.ledger import StockLedgerSnapshotService
import test_split_correction as split_tests


database = split_tests.database
split_case = split_tests.split_case


def _approve_split(connection: Connection, case: CorporateActionManualCaseRecord) -> None:
    """Store a verified split so its accounting consequences can be exercised.

    Args:
        connection: Caller-owned connection containing the test transaction.
        case: Manual case identifying the split and its broker source.

    Returns:
        None: Approval writes remain in the caller's transaction.

    Raises:
        SQLAlchemyError: If an approval database write fails.
    """
    connection.execute(text(
        "UPDATE corporate_action_manual_case c SET split_factor=1.5, status='resolved', "
        "resolution_source_raw_record_id=e.source_raw_record_id, resolution_report_date_local=e.report_date_local "
        "FROM event_corp_action e WHERE c.event_corp_action_id=e.event_corp_action_id AND c.case_id=:id"
    ), {'id': case.case_id})
    connection.execute(text(
        'UPDATE event_corp_action SET requires_manual=false, provisional=false WHERE event_corp_action_id=:id'
    ), {'id': case.event_corp_action_id})


def test_successful_revision_repairs_without_committing_caller_transaction(
    database: Engine, split_case: tuple[object, CorporateActionManualCaseRecord, TestClient],
) -> None:
    """Verify repaired projections remain private until the caller commits.

    Args:
        database: Isolated PostgreSQL engine holding the split's accounting.
        split_case: Seeded split harness, manual case, and HTTP test client.

    Returns:
        None: Assertions verify repair and caller-controlled rollback.

    Raises:
        AssertionError: If repair commits or changes the expected projections.
        SQLAlchemyError: If an accounting database operation fails.
    """
    _, case, _ = split_case
    before = split_tests._state(database)
    with database.connect() as connection, connection.begin() as transaction:
        with CorporateActionAccountingRevision(connection).split_revision('INTEGRITY', case.event_corp_action_id):
            _approve_split(connection, case)
        assert transaction.is_active
        assert connection.execute(text('SELECT position_qty, cost_basis, provisional FROM pnl_snapshot_daily')).one() == (3, 201, False)
        assert connection.scalar(text("SELECT remaining_quantity FROM position_lot WHERE status='open'")) == 3
        assert split_tests._state(database) == before
        transaction.rollback()
    assert split_tests._state(database) == before


def test_body_failure_skips_repair_without_rolling_back_caller_writes(
    database: Engine, split_case: tuple[object, CorporateActionManualCaseRecord, TestClient],
) -> None:
    """Catch an injected evidence failure and verify repair leaves caller writes intact.

    Args:
        database: Isolated PostgreSQL engine holding the split's accounting.
        split_case: Seeded split harness, manual case, and HTTP test client.

    Returns:
        None: The expected RuntimeError is caught and rollback ownership is verified.

    Raises:
        AssertionError: If the exception, transaction, or accounting state changes.
        pytest.fail.Exception: If the context suppresses the injected RuntimeError.
        SQLAlchemyError: If an accounting database operation fails.
    """
    _, case, _ = split_case
    persisted_before = split_tests._state(database)
    failure = RuntimeError('Evidence write failed')
    with database.connect() as connection, connection.begin() as transaction:
        before = accounting_inputs(connection, 'INTEGRITY')
        with pytest.raises(RuntimeError) as raised:
            with CorporateActionAccountingRevision(connection).split_revision('INTEGRITY', case.event_corp_action_id):
                _approve_split(connection, case)
                raise failure
        assert raised.value is failure
        assert transaction.is_active
        assert connection.scalar(text('SELECT requires_manual FROM event_corp_action')) is False
        after = accounting_inputs(connection, 'INTEGRITY')
        assert after['pnl_snapshot_daily'] == before['pnl_snapshot_daily']
        assert after['position_lot'] == before['position_lot']
        transaction.rollback()
    assert split_tests._state(database) == persisted_before


def test_repair_failure_propagates_for_outer_transaction_rollback(
    database: Engine, split_case: tuple[object, CorporateActionManualCaseRecord, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch a failed repair and verify the caller rolls back evidence and projections.

    Args:
        database: Isolated PostgreSQL engine holding the split's accounting.
        split_case: Seeded split harness, manual case, and HTTP test client.
        monkeypatch: Fixture installing the repair failure after projection writes.

    Returns:
        None: The expected RuntimeError is caught and persisted rollback is verified.

    Raises:
        AssertionError: If the original failure or persisted accounting is altered.
        pytest.fail.Exception: If repair suppresses the injected RuntimeError.
        SQLAlchemyError: If an accounting database operation fails unexpectedly.
    """
    _, case, _ = split_case
    before = split_tests._state(database)
    failure = RuntimeError('Accounting repair failed after projection writes')
    build = StockLedgerSnapshotService.ledger_snapshot_build_and_persist

    def fail_after_writes(self: StockLedgerSnapshotService, **kwargs: Any) -> NoReturn:
        """Inject a failure after the ledger has written its projections.

        Args:
            self: Ledger module whose original snapshot method performs the writes.
            **kwargs: Original snapshot arguments forwarded without modification.

        Returns:
            Never returns: Successful writes are followed by the injected failure.

        Raises:
            RuntimeError: The intentional repair failure caught by the enclosing test.
            SQLAlchemyError: If the original projection database writes fail.
        """
        build(self, **kwargs)
        raise failure

    monkeypatch.setattr(StockLedgerSnapshotService, 'ledger_snapshot_build_and_persist', fail_after_writes)
    with pytest.raises(RuntimeError) as raised:
        with database.begin() as connection:
            with CorporateActionAccountingRevision(connection).split_revision('INTEGRITY', case.event_corp_action_id):
                _approve_split(connection, case)
    assert raised.value is failure
    assert split_tests._state(database) == before
