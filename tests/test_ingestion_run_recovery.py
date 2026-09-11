"""Whole-run ownership protects live workers and recovers only abandoned work."""
from datetime import date
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from app.db import SQLAlchemyIngestionRunService, IngestionRunAlreadyActiveError
from test_ingestion_integrity_regressions import _harness, database as _database


database = _database


def test_guard_rejects_another_worker_and_releases_after_error(database):
    first = SQLAlchemyIngestionRunService(database)
    second = SQLAlchemyIngestionRunService(database)

    def try_other_worker():
        with pytest.raises(IngestionRunAlreadyActiveError):
            with second.db_ingestion_run_guard('LOCKED'):
                pytest.fail('a second worker acquired a live lock')

    with pytest.raises(RuntimeError, match='worker failed'):
        with first.db_ingestion_run_guard('LOCKED'):
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(try_other_worker).result(timeout=5)
            raise RuntimeError('worker failed')
    with second.db_ingestion_run_guard('LOCKED'):
        pass


def test_process_interruption_can_be_recovered_by_a_new_worker(database, monkeypatch):
    orchestrator, adapter, *_ = _harness(database)

    def interrupted(**kwargs):
        raise SystemExit('simulated worker exit')

    monkeypatch.setattr(adapter, 'adapter_fetch_report', interrupted)
    with pytest.raises(SystemExit):
        orchestrator.job_execute('ingestion_run')
    fresh = _harness(database)[0]
    assert fresh.job_execute('ingestion_run').status == 'success'
    with database.connect() as connection:
        statuses = connection.execute(text('SELECT status, error_code FROM ingestion_run ORDER BY started_at_utc')).all()
    assert statuses == [('failed', 'INGESTION_RUN_INTERRUPTED'), ('success', None)]


def test_new_atomic_failures_do_not_disable_duplicate_skip(database, monkeypatch):
    orchestrator, adapter, _, canonical, *_ = _harness(database)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    fetch = adapter.adapter_fetch_report

    def failure(**kwargs):
        raise ConnectionError('temporary upstream failure')

    monkeypatch.setattr(adapter, 'adapter_fetch_report', failure)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    monkeypatch.setattr(adapter, 'adapter_fetch_report', fetch)
    assert canonical.db_canonical_skip_is_safe('INTEGRITY')
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    with database.connect() as connection:
        diagnostics = connection.scalar(text('SELECT diagnostics FROM ingestion_run ORDER BY started_at_utc DESC LIMIT 1'))
    assert any(item.get('details', {}).get('canonical_skip_reason') == 'exact_duplicate_artifact' for item in diagnostics)


def test_legacy_failed_run_still_disables_incremental_skip(database):
    _, _, _, canonical, _, _, runs = _harness(database)
    legacy = runs.db_ingestion_run_create_started('INTEGRITY', 'manual', '2026-08-21', 'query', date(2026, 8, 21))
    runs.db_ingestion_run_finalize(legacy.ingestion_run_id, 'failed', 'OLD_FAILURE', 'legacy partial write', [])
    assert not canonical.db_canonical_skip_is_safe('INTEGRITY')


def test_lost_lock_connection_cannot_publish_semantics(database):
    from app.db import SQLAlchemyCanonicalPersistenceService
    from app.db.session import db_connection_scope
    from sqlalchemy.exc import SQLAlchemyError

    runs = SQLAlchemyIngestionRunService(database)
    canonical = SQLAlchemyCanonicalPersistenceService(database)
    with database.begin() as connection:
        connection.execute(text('CREATE TABLE publication_probe (value integer)'))
    with pytest.raises(SQLAlchemyError):
        with runs.db_ingestion_run_guard('LOST'):
            with database.connect() as connection:
                owner = connection.scalar(text("SELECT pid FROM pg_locks WHERE locktype='advisory' AND granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database())"))
                assert owner is not None
                assert connection.scalar(text('SELECT pg_terminate_backend(:pid)'), {'pid': owner})
            with canonical.db_canonical_transaction():
                # The publication must use the same session as lock ownership.
                with db_connection_scope(database, write=True) as connection:
                    connection.execute(text('INSERT INTO publication_probe VALUES (1)'))
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM publication_probe')) == 0


def test_import_guard_and_manual_split_lock_exclude_each_other(database):
    from app.db.corporate_action_correction import SQLAlchemySplitCorrectionService, SplitCorrectionConflict

    runs = SQLAlchemyIngestionRunService(database)
    correction = SQLAlchemySplitCorrectionService(database, 'LOCKED')
    with runs.db_ingestion_run_guard('LOCKED'):
        with database.begin() as connection:
            with pytest.raises(SplitCorrectionConflict):
                correction._lock_account(connection)
    with database.begin() as connection:
        correction._lock_account(connection)
        with pytest.raises(IngestionRunAlreadyActiveError):
            with runs.db_ingestion_run_guard('LOCKED'):
                pytest.fail('import started during a manual split correction')
