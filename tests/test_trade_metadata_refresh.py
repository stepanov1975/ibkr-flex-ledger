"""Validated trade metadata refreshes retain origins and rebuild the same ledger."""

from decimal import Decimal
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy import text

from app.db.portfolio import SQLAlchemyPortfolioService
from test_end_to_end_seeded import _SEEDED_PAYLOAD
from test_ingestion_integrity_regressions import _harness, _replay, database as _database


database = _database


def _report(**metadata):
    root = ET.fromstring(_SEEDED_PAYLOAD)
    root.find('.//OpenPosition').set('multiplier', '1')
    root.find('.//Trade').attrib.update(multiplier='1', ibCommissionCurrency='USD')
    root.find('.//Trade').attrib.update(metadata)
    ET.SubElement(root.find('.//ConversionRates'), 'ConversionRate', {
        'fromCurrency': 'EUR', 'toCurrency': 'USD', 'reportDate': '20260821', 'rate': '2',
    })
    return ET.tostring(root)


@pytest.mark.parametrize('attribute,value,field,expected_totals', [
    ('closePrice', '120', 'close_price', (201, 19, 1)),
    ('multiplier', '2', 'multiplier', (401, -181, 1)),
    ('ibCommissionCurrency', 'EUR', 'commission_currency', (202, 18, 2)),
])
def test_live_and_replay_use_refreshed_trade_metadata(database, attribute, value, field, expected_totals):
    harness = _harness(database)
    orchestrator, adapter, _, _, _, ledger, _ = harness
    adapter.payload_bytes = _report()
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    original = ledger.db_ledger_trade_fill_list_for_account('INTEGRITY')[0]
    root = ET.fromstring(adapter.payload_bytes)
    root.find('.//Trade').set(attribute, value)
    adapter.payload_bytes = ET.tostring(root)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    refreshed = ledger.db_ledger_trade_fill_list_for_account('INTEGRITY')[0]
    actual = getattr(refreshed, field)
    if field == 'commission_currency':
        assert actual == value
        assert ledger.db_ledger_instrument_ids_for_scope('INTEGRITY', (), ('EUR',)) == [str(refreshed.instrument_id)]
        portfolio = SQLAlchemyPortfolioService(database)
        # The summary cannot convert a third-currency commission from the trade's FX alone.
        assert portfolio.db_report_portfolio_summary('INTEGRITY').securities_commission_total_usd is None
        assert Decimal(portfolio.db_reconciliation_sources('INTEGRITY', None, None, None)[0].broker_fees) == 2
    else:
        assert Decimal(actual) == Decimal(value)
    assert refreshed.source_raw_record_id == original.source_raw_record_id
    with database.connect() as connection:
        period = connection.scalar(text('SELECT period_key FROM raw_artifact LIMIT 1'))
        totals = connection.execute(text('SELECT cost_basis, unrealized_pnl, fees FROM pnl_snapshot_daily')).all()
    assert totals == [expected_totals]
    assert _replay(harness, period).status == 'success'
    assert ledger.db_ledger_trade_fill_list_for_account('INTEGRITY')[0] == refreshed
    with database.connect() as connection:
        assert connection.execute(text('SELECT cost_basis, unrealized_pnl, fees FROM pnl_snapshot_daily')).all() == totals


def test_failed_metadata_refresh_leaves_published_values_intact(database, monkeypatch):
    harness = _harness(database)
    orchestrator, adapter, _, _, snapshots, ledger, _ = harness
    adapter.payload_bytes = _report()
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    original = ledger.db_ledger_trade_fill_list_for_account('INTEGRITY')[0]
    adapter.payload_bytes = _report(closePrice='120')

    def fail_snapshot(**kwargs):
        raise RuntimeError('snapshot failed after metadata refresh')

    monkeypatch.setattr(snapshots, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    assert ledger.db_ledger_trade_fill_list_for_account('INTEGRITY')[0] == original


@pytest.mark.parametrize('metadata', [
    {'description': 'Updated description'},
    {'closePrice': '110.000', 'multiplier': '1.000', 'ibCommissionCurrency': ' usd '},
])
def test_equivalent_metadata_does_not_invalidate_trade_accounting(database, metadata):
    orchestrator, adapter, *_ = _harness(database)
    adapter.payload_bytes = _report()
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    with database.connect() as connection:
        modified = connection.scalar(text('SELECT updated_at_utc FROM event_trade_fill'))
    adapter.payload_bytes = _report(**metadata)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT updated_at_utc FROM event_trade_fill')) == modified
