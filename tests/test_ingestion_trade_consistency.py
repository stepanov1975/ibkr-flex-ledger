"""Live imports reject conflicting broker executions without publishing them."""
from decimal import Decimal

import pytest
from sqlalchemy import text

from test_end_to_end_seeded import _SEEDED_PAYLOAD
from test_ingestion_integrity_regressions import _harness, database as _database


database = _database


@pytest.mark.parametrize('old,new,field', [
    (b'tradePrice="100"', b'tradePrice="200"', 'price'),
    (b'ibExecID="SEED-EXEC-1"', b'ibExecID="CHANGED"', 'ib_exec_id'),
])
def test_changed_execution_is_retained_and_rejected(database, old, new, field):
    orchestrator, adapter = _harness(database)[:2]
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    assert old in _SEEDED_PAYLOAD
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(old, new)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    with database.connect() as connection:
        run = connection.execute(text('SELECT error_code,error_message FROM ingestion_run ORDER BY started_at_utc DESC LIMIT 1')).one()
        assert run.error_code == 'TRADE_CONSISTENCY_CONFLICT'
        assert field in run.error_message
        assert connection.scalar(text('SELECT count(*) FROM event_trade_fill')) == 1
        assert connection.scalar(text('SELECT price FROM event_trade_fill')) == Decimal('100')
        assert connection.scalar(text('SELECT count(*) FROM raw_artifact')) == 2
        assert connection.scalar(text('SELECT count(*) FROM raw_artifact WHERE completed_ingestion_run_id IS NOT NULL')) == 1
