"""Rejected report contexts remain retained but cannot enter the ledger."""
from decimal import Decimal
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

from app.db import SQLAlchemyPortfolioService
from app.db.account_insights import db_account_insights
from test_end_to_end_seeded import _SEEDED_PAYLOAD
from test_ingestion_integrity_regressions import _harness, _replay, database as _database


database = _database


@pytest.mark.parametrize('kind', [
    'eur_base', 'linked_accounts', 'different_account', 'row_mismatch', 'stale_position', 'invalid_position_date',
])
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
    elif kind == 'row_mismatch':
        root.find('.//Trade').set('accountId', 'U_OTHER')
    else:
        root.find('.//OpenPosition').set('reportDate', '20260820' if kind == 'stale_position' else '2026-02-30')
    adapter.payload_bytes = ET.tostring(root)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT error_code FROM ingestion_run ORDER BY started_at_utc DESC LIMIT 1')) == 'REPORT_CONTEXT_INVALID'
        assert connection.scalar(text('SELECT count(*) FROM raw_artifact')) == 2
        assert connection.scalar(text('SELECT count(*) FROM event_trade_fill')) == 1
        period = connection.scalar(text('SELECT period_key FROM raw_artifact LIMIT 1'))
    assert raw.db_raw_successful_broker_account_ids('INTEGRITY') == frozenset({'U_TEST'})
    assert len(canonical.db_raw_artifact_replay_candidate_list('INTEGRITY', period, 'seeded-query')) == 1


@pytest.mark.parametrize('stale_date', ['20260820', '2026-02-30'])
def test_replay_rebuilds_existing_snapshot_without_stale_position_valuation(database, stale_date):
    harness = _harness(database)
    orchestrator, adapter = harness[:2]
    root = ET.fromstring(_SEEDED_PAYLOAD)
    position = root.find('.//OpenPosition')
    position.set('multiplier', '1')
    position.set('reportDate', '2026/08/21')
    adapter.payload_bytes = ET.tostring(root)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    position.set('markPrice', '120')
    position.set('positionValue', '240')
    adapter.payload_bytes = ET.tostring(root)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    portfolio = SQLAlchemyPortfolioService(database)
    assert Decimal(portfolio.db_report_portfolio_summary('INTEGRITY').estimated_net_liquidation_value_usd) == 270
    assert db_account_insights(database, 'INTEGRITY')['concentration']['holdings'][0]['value_usd'] == 240
    assert Decimal(portfolio.db_reconciliation_sources('INTEGRITY', None, None, None)[0].broker_position_qty) == 2
    with database.begin() as connection:
        assert connection.execute(text('SELECT unrealized_pnl, provisional FROM pnl_snapshot_daily')).one() == (
            Decimal('39'), False,
        )
        period = connection.scalar(text('SELECT period_key FROM raw_artifact'))
        # Simulate immutable evidence retained by a version that did not check the row date.
        connection.execute(text(
            "UPDATE raw_record SET source_payload = jsonb_set(source_payload, '{reportDate}', to_jsonb(CAST(:day AS text))) "
            "WHERE section_name = 'OpenPositions' AND source_payload->>'positionValue'='240'"
        ), {'day': stale_date})

    assert portfolio.db_report_portfolio_summary('INTEGRITY').estimated_net_liquidation_value_usd is None
    assert 'OpenPositions' not in db_account_insights(database, 'INTEGRITY')['sections']
    comparison, = portfolio.db_reconciliation_sources('INTEGRITY', None, None, None)
    assert comparison.broker_position_qty is None
    assert comparison.broker_unrealized_pnl is None
    assert comparison.provisional is True
    assert _replay(harness, period).status == 'success'
    with database.connect() as connection:
        assert connection.execute(text(
            'SELECT position_qty, cost_basis, unrealized_pnl, provisional, valuation_source FROM pnl_snapshot_daily'
        )).one() == (Decimal('2'), None, Decimal('0'), True, 'openpositions_summary_unavailable')


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


def test_header_only_successful_identity_lookup_does_not_reload_artifact_payload(database):
    orchestrator, adapter, raw, *_ = _harness(database)
    root = ET.fromstring(_SEEDED_PAYLOAD)
    root.find('.//AccountInformation').attrib.pop('accountId')
    root.find('.//FlexStatement').set('accountId', 'U_HEADER')
    adapter.payload_bytes = ET.tostring(root)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    assert raw.db_raw_successful_broker_account_ids('INTEGRITY') == frozenset({'U_HEADER'})

    with database.begin() as connection:
        connection.execute(text("UPDATE raw_artifact SET source_payload=CAST('unreadable' AS bytea)"))

    assert raw.db_raw_successful_broker_account_ids('INTEGRITY') == frozenset({'U_HEADER'})
