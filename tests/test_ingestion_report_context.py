"""Rejected account/base contexts remain retained but cannot enter the ledger."""
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

from test_end_to_end_seeded import _SEEDED_PAYLOAD
from test_ingestion_integrity_regressions import _harness, database as _database


database = _database


@pytest.mark.parametrize('kind', ['eur_base', 'linked_accounts', 'different_account', 'row_mismatch'])
def test_invalid_context_is_retained_without_publishing(database, kind):
    orchestrator, adapter, raw, canonical, *_ = _harness(database)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    root = ET.fromstring(_SEEDED_PAYLOAD)
    if kind == 'eur_base':
        root.find('.//AccountInformation').set('currency', 'EUR')
    elif kind == 'linked_accounts':
        root.find('.//FlexStatements').set('count', '2')
        root.find('.//FlexStatements').append(ET.fromstring(ET.tostring(root.find('.//FlexStatement'))))
    elif kind == 'different_account':
        root.find('.//AccountInformation').set('accountId', 'U_OTHER')
    else:
        root.find('.//Trade').set('accountId', 'U_OTHER')
    adapter.payload_bytes = ET.tostring(root)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT error_code FROM ingestion_run ORDER BY started_at_utc DESC LIMIT 1')) == 'REPORT_CONTEXT_INVALID'
        assert connection.scalar(text('SELECT count(*) FROM raw_artifact')) == 2
        assert connection.scalar(text('SELECT count(*) FROM event_trade_fill')) == 1
        period = connection.scalar(text('SELECT period_key FROM raw_artifact LIMIT 1'))
    assert raw.db_raw_successful_broker_account_ids('INTEGRITY') == frozenset({'U_TEST'})
    assert len(canonical.db_raw_artifact_replay_candidate_list('INTEGRITY', period, 'seeded-query')) == 1


def test_failed_report_does_not_bind_account_and_header_only_success_does(database):
    orchestrator, adapter, raw, *_ = _harness(database)
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b'accountId="U_TEST" currency="USD"', b'accountId="WRONG" currency="EUR"')
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    assert raw.db_raw_successful_broker_account_ids('INTEGRITY') == frozenset()
    root = ET.fromstring(_SEEDED_PAYLOAD)
    root.find('.//AccountInformation').attrib.pop('accountId')
    root.find('.//FlexStatement').set('accountId', 'U_HEADER')
    adapter.payload_bytes = ET.tostring(root)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    assert raw.db_raw_successful_broker_account_ids('INTEGRITY') == frozenset({'U_HEADER'})
    root.find('.//FlexStatement').set('accountId', 'U_OTHER')
    adapter.payload_bytes = ET.tostring(root)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
