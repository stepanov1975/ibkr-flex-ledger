"""Review reproduction: a committed publication must retain successful audit."""
import psycopg
import pytest
from sqlalchemy import text

from test_ingestion_integrity_regressions import (
    _harness,
    _replay,
    database as _database,
)


database = _database


@pytest.mark.parametrize("replay", [False, True])
def test_lost_commit_acknowledgment_preserves_success(database, monkeypatch, replay):
    harness = _harness(database)
    orchestrator, _, _, canonical, _, _, runs = harness
    if replay:
        assert orchestrator.job_execute("ingestion_run").status == "success"
        with database.connect() as connection:
            period = connection.scalar(text("SELECT period_key FROM raw_artifact LIMIT 1"))

    original_finalize = runs.db_ingestion_run_finalize
    original_commit = database.dialect.do_commit
    armed = False
    injected = False

    def finalize(**kwargs):
        nonlocal armed
        result = original_finalize(**kwargs)
        if kwargs["status"] == "success":
            armed = True
        return result

    def commit(dbapi_connection):
        nonlocal armed, injected
        original_commit(dbapi_connection)
        if armed:
            armed = False
            injected = True
            dbapi_connection.driver_connection.close()
            raise psycopg.OperationalError("connection is closed; simulated lost COMMIT acknowledgment")

    monkeypatch.setattr(runs, "db_ingestion_run_finalize", finalize)
    monkeypatch.setattr(database.dialect, "do_commit", commit)

    result = _replay(harness, period) if replay else orchestrator.job_execute("ingestion_run")
    assert injected
    assert result.status == "success"
    with database.connect() as connection:
        assert connection.scalar(text("SELECT status FROM ingestion_run ORDER BY started_at_utc DESC LIMIT 1")) == "success"
        assert connection.scalar(text("SELECT count(*) FROM event_trade_fill")) == 1
        assert connection.scalar(text("SELECT count(*) FROM pnl_snapshot_daily")) == 1
        period = connection.scalar(text("SELECT period_key FROM raw_artifact LIMIT 1"))
    assert len(canonical.db_raw_artifact_replay_candidate_list("INTEGRITY", period, "seeded-query")) == 1
