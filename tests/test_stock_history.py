"""Stock-family history against imported trades and the actual FIFO projection."""

import os
from decimal import Decimal
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from app.api.routers.reports import api_create_reports_router
from app.config import AppSettings
from app.db import (
    SQLAlchemyCanonicalPersistenceService,
    SQLAlchemyIngestionRunService,
    SQLAlchemyLedgerSnapshotService,
    SQLAlchemyPortfolioService,
    SQLAlchemyRawPersistenceService,
    db_create_engine,
)
from app.jobs import IngestionJobOrchestrator, IngestionOrchestratorConfig
from app.ledger import StockLedgerSnapshotService
from app.db.stock_history import _stock_family
from test_end_to_end_seeded import (
    _SeededAdapter, _create_database, _database_url, _drop_database, _reachable_database_url,
)
from test_ingestion_integrity_regressions import _harness


_PAYLOAD = b'''<FlexQueryResponse><FlexStatements count="1"><FlexStatement reportDate="20260821">
<Trades>
 <Trade ibExecID="S1" transactionID="1" conid="101" symbol="TEST" assetCategory="STK"
  currency="USD" buySell="BUY" quantity="10" tradePrice="100" ibCommission="-2"
  reportDate="20260820" dateTime="20260820;100000" />
 <Trade ibExecID="S2" transactionID="2" conid="101" symbol="TEST" assetCategory="STK"
  currency="USD" buySell="SELL" quantity="4" tradePrice="120" ibCommission="-1"
  reportDate="20260821" dateTime="20260821;100000" />
 <Trade ibExecID="O1" transactionID="3" conid="102" symbol="TEST  260918P00100000" assetCategory="OPT"
  currency="USD" buySell="SELL" quantity="2" tradePrice="3" multiplier="100" ibCommission="-2"
  reportDate="20260820" dateTime="20260820;110000" underlyingConid="101" underlyingSymbol="TEST" />
 <Trade ibExecID="O2" transactionID="4" conid="102" symbol="TEST  260918P00100000" assetCategory="OPT"
  currency="USD" buySell="BUY" quantity="1" tradePrice="1" multiplier="100" ibCommission="-1"
  reportDate="20260821" dateTime="20260821;110000" />
 <Trade ibExecID="C1" transactionID="5" conid="103" symbol="TEST  260821C00100000" assetCategory="OPT"
  currency="USD" buySell="BUY" quantity="1" tradePrice="2" multiplier="100"
  reportDate="20260820" dateTime="20260820;120000" />
 <Trade ibExecID="C2" transactionID="6" conid="103" symbol="TEST  260821C00100000" assetCategory="OPT"
  currency="USD" buySell="SELL" quantity="1" tradePrice="4" multiplier="100"
  reportDate="20260821" dateTime="20260821;120000" />
 <Trade ibExecID="X1" transactionID="7" conid="104" symbol="TESTX 260918P00100000" assetCategory="OPT"
  currency="USD" buySell="BUY" quantity="1" tradePrice="1" multiplier="100"
  reportDate="20260820" dateTime="20260820;130000" />
 <Trade ibExecID="X2" transactionID="8" conid="105" symbol="TEST  260918C00100000" assetCategory="OPT"
  currency="USD" buySell="BUY" quantity="1" tradePrice="1" multiplier="100"
  reportDate="20260820" dateTime="20260820;140000" underlyingConid="999" underlyingSymbol="OTHER" />
</Trades>
<OpenPositions>
 <OpenPosition conid="101" symbol="TEST" assetCategory="STK" currency="USD"
  position="6" markPrice="130" multiplier="1" reportDate="20260821" />
 <OpenPosition conid="102" symbol="TEST  260918P00100000" assetCategory="OPT" currency="USD"
  position="-1" markPrice="0.5" multiplier="100" reportDate="20260821" />
</OpenPositions>
<CashTransactions><CashTransaction transactionID="9" conid="101" symbol="TEST" assetCategory="STK"
 type="Dividends" amount="5" currency="USD" reportDate="20260821" /></CashTransactions>
<CorporateActions /><ConversionRates /><SecuritiesInfo /><AccountInformation />
</FlexStatement></FlexStatements></FlexQueryResponse>'''


_ROUNDING_PAYLOAD = ('''<FlexQueryResponse><FlexStatements count="1"><FlexStatement reportDate="20260821">
<Trades>''' + ''.join(f'''
 <Trade ibExecID="FX{i}" transactionID="{i}" conid="101" symbol="ROUND" assetCategory="STK"
 currency="EUR" buySell="BUY" quantity="1" tradePrice="10.01" fxRateToBase="1.123456789"
 reportDate="20260821" dateTime="20260821;10000{i}" />''' for i in range(1, 4)) + '''</Trades>
<OpenPositions><OpenPosition conid="101" symbol="ROUND" assetCategory="STK" currency="EUR"
 position="3" markPrice="11" multiplier="1" fxRateToBase="1.123456789" reportDate="20260821" /></OpenPositions>
<CashTransactions /><CorporateActions /><ConversionRates /><SecuritiesInfo /><AccountInformation />
</FlexStatement></FlexStatements></FlexQueryResponse>''').encode()


@pytest.fixture
def history_database(request):
    base_url = _reachable_database_url()
    name = f"test_history_{uuid4().hex[:10]}"
    admin_url = _database_url(base_url, "postgres")
    url = _database_url(base_url, name)
    previous_url = os.environ.get("DATABASE_URL")
    _create_database(admin_url, name)
    os.environ["DATABASE_URL"] = url
    engine = None
    try:
        command.upgrade(Config("alembic.ini"), "head")
        engine = db_create_engine(url)
        orchestrator = IngestionJobOrchestrator(
            ingestion_repository=SQLAlchemyIngestionRunService(engine),
            raw_persistence_repository=SQLAlchemyRawPersistenceService(engine),
            flex_adapter=_SeededAdapter(getattr(request, 'param', _PAYLOAD)),
            config=IngestionOrchestratorConfig(account_id="HISTORY", flex_query_id="seeded-query"),
            canonical_repository=SQLAlchemyCanonicalPersistenceService(engine),
            snapshot_service=StockLedgerSnapshotService(SQLAlchemyLedgerSnapshotService(engine)),
        )
        result = orchestrator.job_execute("ingestion_run")
        with engine.connect() as connection:
            failure = connection.execute(text('SELECT error_code, error_message FROM ingestion_run')).all()
        assert result.status == "success", failure
        repository = SQLAlchemyPortfolioService(engine)
        application = FastAPI()
        application.include_router(api_create_reports_router(
            AppSettings(ibkr_flex_token="test", ibkr_flex_query_id="test", account_id="HISTORY"), repository,
        ))
        with engine.connect() as connection:
            ids = dict(connection.execute(text("SELECT conid, instrument_id FROM instrument")).all())
        yield TestClient(application), repository, ids, engine
    finally:
        if engine is not None:
            engine.dispose()
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        _drop_database(admin_url, name)


def test_stock_history_includes_options_partial_closes_and_cashflows(history_database):
    client, repository, ids, _ = history_database
    response = client.get(f"/reports/stock-history/{ids['101']}")
    assert response.status_code == 200
    report = response.json()
    assert {row['conid'] for row in report['positions']} == {'101', '102', '103'}
    total = report['totals'][0]
    assert total['currency'] == 'USD'
    assert Decimal(total['realized_pnl']) == Decimal('481.2')
    assert Decimal(total['unrealized_pnl']) == Decimal('427.8')
    lots = {row['conid']: row for row in report['lots']}
    assert lots['101']['status'] == 'partially_closed'
    assert Decimal(lots['101']['realized_pnl']) == Decimal('78.2')
    assert Decimal(lots['101']['unrealized_pnl']) == Decimal('178.8')
    assert Decimal(lots['102']['remaining_quantity']) == Decimal('-1')
    assert Decimal(lots['102']['realized_pnl']) == Decimal('198')
    assert Decimal(lots['102']['unrealized_pnl']) == Decimal('249')
    assert lots['103']['status'] == 'closed'
    assert Decimal(lots['103']['realized_pnl']) == Decimal('200')
    assert Decimal(lots['103']['unrealized_pnl']) == 0
    assert len(report['activity']) == 7
    assert sum(row['event_type'] == 'trade' for row in report['activity']) == 6
    assert any(row['action'] == 'Dividends' and Decimal(row['amount']) == 5 for row in report['activity'])
    assert repository.db_report_stock_history('OTHER_ACCOUNT', ids['101']) is None
    assert client.get(f"/reports/stock-history/{uuid4()}").status_code == 404
    assert client.get('/reports/stock-history/not-a-uuid').status_code == 422


def test_option_link_resolves_to_the_same_stock_family(history_database):
    client, _, ids, _ = history_database
    response = client.get(f"/reports/stock-history/{ids['102']}")
    assert response.status_code == 200
    report = response.json()
    assert report['instrument_id'] == str(ids['101'])
    assert {row['conid'] for row in report['positions']} == {'101', '102', '103'}


def test_missing_snapshot_does_not_fabricate_zero_pnl(history_database):
    client, _, ids, engine = history_database
    with engine.begin() as connection:
        connection.execute(text('DELETE FROM pnl_snapshot_daily WHERE instrument_id=:id'), {'id': ids['102']})
    response = client.get(f"/reports/stock-history/{ids['101']}")
    assert response.status_code == 200
    report = response.json()
    assert report['totals'][0]['realized_pnl'] is None
    assert report['totals'][0]['unrealized_pnl'] is None
    option_lot = next(row for row in report['lots'] if row['conid'] == '102')
    assert option_lot['unrealized_pnl'] is None


def test_totals_use_latest_snapshot_and_mismatched_lots_have_no_valuation(history_database):
    client, _, ids, engine = history_database
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO pnl_snapshot_daily (account_id, instrument_id, report_date_local, position_qty, "
            "realized_pnl, unrealized_pnl, total_pnl, currency) "
            "VALUES ('HISTORY', :id, '2026-08-19', 10, 1000, 1000, 2000, 'USD')"
        ), {'id': ids['101']})
        connection.execute(text(
            "UPDATE pnl_snapshot_daily SET position_qty=7, provisional=true "
            "WHERE instrument_id=:id AND report_date_local='2026-08-21'"
        ), {'id': ids['101']})
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['provisional'] is True
    assert Decimal(report['totals'][0]['realized_pnl']) == Decimal('481.2')
    stock_lot = next(row for row in report['lots'] if row['conid'] == '101')
    assert stock_lot['unrealized_pnl'] is None
    assert Decimal(stock_lot['realized_pnl']) == Decimal('78.2')


def test_history_includes_corporate_actions_with_conid_only(history_database):
    client, _, ids, engine = history_database
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO event_corp_action (account_id, conid, ingestion_run_id, source_raw_record_id, "
            "action_id, reorg_code, report_date_local, description) "
            "SELECT account_id, '101', ingestion_run_id, source_raw_record_id, 'HISTORY-CA', 'SPINOFF', "
            "report_date_local, 'Stock distribution' FROM event_trade_fill "
            "WHERE instrument_id=:id ORDER BY trade_timestamp_utc LIMIT 1"
        ), {'id': ids['101']})
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    actions = [row for row in report['activity'] if row['event_type'] == 'corporate_action']
    assert len(actions) == 1
    assert actions[0]['description'] == 'Stock distribution'
    assert actions[0]['symbol'] == 'TEST'


@pytest.mark.parametrize('history_database', [_ROUNDING_PAYLOAD], indirect=True, ids=['fx-rounding'])
def test_independently_rounded_fx_lots_keep_their_unrealized_pnl(history_database):
    client, _, ids, _ = history_database
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['provisional'] is False
    assert len(report['lots']) == 3
    for lot in report['lots']:
        assert lot['unrealized_pnl'] is not None
        assert abs(Decimal(lot['unrealized_pnl']) - Decimal('1.11222222')) <= Decimal('0.00000001')
    assert Decimal(report['totals'][0]['unrealized_pnl']) == Decimal('3.33666666')


@pytest.mark.parametrize('selected_index', [0, 1])
def test_option_only_family_combines_underlying_id_and_symbol_fallback(selected_index):
    instruments = [
        {'instrument_id': uuid4(), 'conid': '102', 'asset_category': 'OPT',
         'symbol': 'TEST  260918P00100000', 'underlying_conid': '101', 'underlying_symbol': None},
        {'instrument_id': uuid4(), 'conid': '103', 'asset_category': 'OPT',
         'symbol': 'TEST  260918C00100000', 'underlying_conid': None, 'underlying_symbol': None},
    ]
    _, family = _stock_family(instruments[selected_index], instruments)
    assert {row['conid'] for row in family} == {'102', '103'}


@pytest.mark.parametrize('history_database', [
    _PAYLOAD.replace(b'underlyingConid="101" underlyingSymbol="TEST"', b''),
], indirect=True, ids=['new-metadata'])
def test_option_metadata_survives_successful_retry_of_failed_artifact(history_database, monkeypatch):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    adapter.payload_bytes = _PAYLOAD.replace(b'TEST  260918P00100000', b'ADJUSTED OPTION')
    build = service.ledger_snapshot_build_and_persist

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after canonical commit')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', build)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert '102' in {row['conid'] for row in report['positions']}
    option_report = client.get(f"/reports/stock-history/{ids['102']}").json()
    assert option_report['instrument_id'] == str(ids['101'])


def test_newer_symbol_only_metadata_preserves_older_underlying_id(history_database):
    client, _, ids, engine = history_database
    orchestrator, adapter, *_ = _harness(engine, account='HISTORY')
    adapter.payload_bytes = _PAYLOAD.replace(
        b'<SecuritiesInfo />',
        b'<SecuritiesInfo><SecurityInfo conid="102" underlyingSymbol="OLD_TEST" /></SecuritiesInfo>',
    )
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert '102' in {row['conid'] for row in report['positions']}
    option_report = client.get(f"/reports/stock-history/{ids['102']}").json()
    assert option_report['instrument_id'] == str(ids['101'])


@pytest.mark.parametrize('section,record,event_type', [
    ('CashTransactions', '<CashTransaction transactionID="20" conid="106" symbol="ADJUSTED OPTION" '
     'assetCategory="OPT" currency="USD" type="Dividends" amount="2" reportDate="20260821" '
     'underlyingConid="101" underlyingSymbol="TEST" />', 'cashflow'),
    ('CorporateActions', '<CorporateAction actionID="20" transactionID="20" conid="106" '
     'symbol="ADJUSTED OPTION" assetCategory="OPT" currency="USD" type="SPINOFF" reportDate="20260821" '
     'underlyingConid="101" underlyingSymbol="TEST" />', 'corporate_action'),
], ids=['cashflow-option', 'corporate-action-option'])
def test_option_metadata_from_all_instrument_activity(history_database, section, record, event_type):
    client, _, ids, engine = history_database
    orchestrator, adapter, *_ = _harness(engine, account='HISTORY')
    payload = _PAYLOAD.replace(b'<CorporateActions />', b'<CorporateActions></CorporateActions>')
    adapter.payload_bytes = payload.replace(f'</{section}>'.encode(), (record+f'</{section}>').encode())
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert '106' in {row['conid'] for row in report['positions']}
    assert any(row['symbol'] == 'ADJUSTED OPTION' and row['event_type'] == event_type for row in report['activity'])


@pytest.mark.parametrize('activity_date', ['20260821', '20260822'], ids=['same-day', 'next-day'])
def test_activity_after_failed_snapshot_marks_pnl_stale_until_retry(history_database, monkeypatch, activity_date):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    new_cashflow = (f'<CashTransaction transactionID="21" conid="101" symbol="TEST" assetCategory="STK" '
                    f'type="Dividends" amount="7" currency="USD" reportDate="{activity_date}" />').encode()
    adapter.payload_bytes = _PAYLOAD.replace(b'</CashTransactions>', new_cashflow+b'</CashTransactions>').replace(
        b'<FlexStatement reportDate="20260821">', f'<FlexStatement reportDate="{activity_date}">'.encode(),
    )
    build = service.ledger_snapshot_build_and_persist

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after canonical commit')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['provisional'] is True
    assert report['stale'] is True
    assert Decimal(report['totals'][0]['realized_pnl']) == Decimal('481.2')
    stock_lot = next(row for row in report['lots'] if row['conid'] == '101')
    assert stock_lot['provisional'] is True
    assert stock_lot['unrealized_pnl'] is None
    assert any(row['event_type'] == 'cashflow' and Decimal(row['amount']) == 7 for row in report['activity'])
    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', build)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['stale'] is False
    assert Decimal(report['totals'][0]['realized_pnl']) == Decimal('488.2')
