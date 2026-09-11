"""Ingestion must retain raw evidence without publishing incomplete ledger work."""
from decimal import Decimal
import xml.etree.ElementTree as ET

from sqlalchemy import text

from test_end_to_end_seeded import _SEEDED_PAYLOAD
from test_ingestion_integrity_regressions import _harness, database as _database


database = _database


def _new_trade_report():
    root = ET.fromstring(_SEEDED_PAYLOAD)
    trade = root.find('.//Trade')
    trade.set('transactionID', '9010')
    trade.set('ibExecID', 'NEW-EXECUTION')
    trade.set('tradePrice', '200')
    return ET.tostring(root)


def test_snapshot_failure_rolls_back_events_and_retains_raw(database, monkeypatch):
    orchestrator, adapter, _, _, service, _, _ = _harness(database)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    adapter.payload_bytes = _new_trade_report()
    build = service.ledger_snapshot_build_and_persist

    def fail_snapshot(**kwargs):
        raise RuntimeError('snapshot failure after canonical write')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM event_trade_fill')) == 1
        assert connection.scalar(text('SELECT count(*) FROM raw_artifact')) == 2
        assert connection.scalar(text('SELECT cost_basis FROM pnl_snapshot_daily')) == Decimal('201')
    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', build)
    root = ET.fromstring(_SEEDED_PAYLOAD)
    root.find('.//Trades').clear()
    adapter.payload_bytes = ET.tostring(root)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT cost_basis FROM pnl_snapshot_daily')) == Decimal('201')


def test_success_finalization_failure_rolls_back_semantics(database, monkeypatch):
    orchestrator, _, _, _, _, _, runs = _harness(database)
    finalize = runs.db_ingestion_run_finalize

    def fail_success(**kwargs):
        if kwargs['status'] == 'success':
            raise RuntimeError('cannot finalize success')
        return finalize(**kwargs)

    monkeypatch.setattr(runs, 'db_ingestion_run_finalize', fail_success)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM event_trade_fill')) == 0
        assert connection.scalar(text('SELECT count(*) FROM position_lot')) == 0
        assert connection.scalar(text('SELECT count(*) FROM pnl_snapshot_daily')) == 0
        assert connection.scalar(text('SELECT completed_ingestion_run_id FROM raw_artifact')) is None
        assert connection.scalar(text('SELECT status FROM ingestion_run')) == 'failed'


def test_preflight_failure_retains_exact_downloaded_payload(database):
    orchestrator, adapter = _harness(database)[:2]
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b'<ConversionRates />', b'')
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT source_payload FROM raw_artifact')) == adapter.payload_bytes
        assert connection.scalar(text('SELECT count(*) FROM event_trade_fill')) == 0


def test_invalid_xml_retains_exact_downloaded_payload(database):
    orchestrator, adapter = _harness(database)[:2]
    adapter.payload_bytes = b'<FlexQueryResponse><truncated'
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT source_payload FROM raw_artifact')) == adapter.payload_bytes


def test_replay_failure_rolls_back_all_dates(database, monkeypatch):
    from test_ingestion_integrity_regressions import _replay

    harness = _harness(database)
    orchestrator, adapter, _, _, service = harness[:5]
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    root = ET.fromstring(_SEEDED_PAYLOAD)
    root.find('.//FlexStatement').set('reportDate', '20260822')
    root.find('.//OpenPosition').set('reportDate', '20260822')
    adapter.payload_bytes = ET.tostring(root)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    with database.begin() as connection:
        period = connection.scalar(text('SELECT period_key FROM raw_artifact LIMIT 1'))
        connection.execute(text('DELETE FROM pnl_snapshot_daily'))
    build = service.ledger_snapshot_build_and_persist
    calls = 0

    def fail_second_date(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('second replay date failed')
        return build(**kwargs)

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_second_date)
    assert _replay(harness, period).status == 'failed'
    assert calls == 2
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM pnl_snapshot_daily')) == 0
