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
from app.jobs import (
    CanonicalReprocessOrchestrator, CanonicalReprocessOrchestratorConfig,
    IngestionJobOrchestrator, IngestionOrchestratorConfig,
)
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

_NO_CASH_PAYLOAD = _PAYLOAD.replace(
    _PAYLOAD.split(b'<CashTransactions>')[1].split(b'</CashTransactions>')[0], b'',
)
_COMMISSION_FX_PAYLOAD = _NO_CASH_PAYLOAD.replace(b'ibCommission="-2"', b'ibCommission="-2" ibCommissionCurrency="GBP"').replace(
    b'<ConversionRates />', b'<ConversionRates><ConversionRate fromCurrency="GBP" toCurrency="USD" '
    b'reportDate="20260820" rate="1.5" /></ConversionRates>',
)

_SUPERSEDED_FX_PAYLOAD = _COMMISSION_FX_PAYLOAD.replace(
    b'</ConversionRates>', b'<ConversionRate fromCurrency="GBP" toCurrency="USD" '
    b'reportDate="20260819" rate="1.2" /></ConversionRates>',
)
_PREVIOUS_FX_PAYLOAD = _COMMISSION_FX_PAYLOAD.replace(
    b'reportDate="20260820" rate="1.5"', b'reportDate="20260819" rate="1.5"',
)
_DIRECT_BROKER_FX_PAYLOAD = _ROUNDING_PAYLOAD.replace(
    b'<ConversionRates />', b'<ConversionRates><ConversionRate fromCurrency="EUR" toCurrency="USD" '
    b'reportDate="20260821" rate="1.2" /></ConversionRates>',
)
_DIRECT_TRADE_FX_PAYLOAD = _DIRECT_BROKER_FX_PAYLOAD.replace(
    _DIRECT_BROKER_FX_PAYLOAD.split(b'<OpenPositions>')[1].split(b'</OpenPositions>')[0], b'',
).replace(
    b'</Trades>', b'<Trade ibExecID="FX-CLOSE" transactionID="4" conid="101" symbol="ROUND" assetCategory="STK" '
    b'currency="EUR" buySell="SELL" quantity="3" tradePrice="11" fxRateToBase="1.123456789" '
    b'reportDate="20260821" dateTime="20260821;110000" /></Trades>',
)
_BASE_CASHFLOW_FX_PAYLOAD = _PAYLOAD.replace(
    b'amount="5" currency="USD"', b'amount="5" amountInBase="5.5" currency="EUR"',
).replace(
    b'<ConversionRates />', b'<ConversionRates><ConversionRate fromCurrency="EUR" toCurrency="USD" '
    b'reportDate="20260821" rate="1.2" /></ConversionRates>',
)
_BASE_NET_CASH_PAYLOAD = _NO_CASH_PAYLOAD.replace(
    b'tradePrice="100"', b'tradePrice="100" netCash="-1002"',
)
_DIRECT_NET_CASH_PAYLOAD = _DIRECT_TRADE_FX_PAYLOAD.replace(
    b'tradePrice="10.01"', b'tradePrice="10.01" netCash="-10.01" netCashInBase="-11.25"', 1,
)
_RATIO_NET_CASH_PAYLOAD = _DIRECT_NET_CASH_PAYLOAD.replace(b' fxRateToBase="1.123456789"', b'')
_CLOSED_BROKER_FX_PAYLOAD = _DIRECT_TRADE_FX_PAYLOAD.replace(
    b'<OpenPositions></OpenPositions>',
    b'<OpenPositions><OpenPosition conid="101" symbol="ROUND" assetCategory="STK" currency="EUR" '
    b'position="0" markPrice="11" multiplier="1" costBasisMoney="0" fifoPnlUnrealized="0" '
    b'reportDate="20260821" /></OpenPositions>',
)


@pytest.mark.parametrize('history_database', [
    _BASE_CASHFLOW_FX_PAYLOAD.replace(b'amountInBase="5.5" ', b''),
], indirect=True, ids=['foreign-cashflow'])
def test_fx_only_import_rebuilds_foreign_cashflow_for_base_currency_instrument(history_database):
    client, _, ids, engine = history_database
    # Later security metadata can differ from a historical cashflow's currency.
    with engine.begin() as connection:
        connection.execute(text("UPDATE instrument SET currency='USD' WHERE conid='101'"))
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert before['stale'] is False
    orchestrator, adapter, *_ = _harness(engine, account='HISTORY')
    adapter.payload_bytes = _BASE_CASHFLOW_FX_PAYLOAD.replace(b'amountInBase="5.5" ', b'').replace(
        b'rate="1.2"', b'rate="1.3"',
    )
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert Decimal(report['totals'][0]['realized_pnl']) == Decimal(before['totals'][0]['realized_pnl']) + Decimal('0.5')
    assert report['stale'] is False
    assert report['provisional'] is False


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


def _history_replay(engine, harness):
    _, _, _, canonical, service, snapshots, runs = harness
    with engine.connect() as connection:
        period = connection.scalar(text("SELECT period_key FROM raw_artifact WHERE account_id='HISTORY' LIMIT 1"))
    return CanonicalReprocessOrchestrator(
        raw_read_repository=canonical,
        canonical_persistence_repository=canonical,
        snapshot_service=service,
        snapshot_repository=snapshots,
        ingestion_repository=runs,
        config=CanonicalReprocessOrchestratorConfig(
            account_id='HISTORY', period_key=period, flex_query_id='seeded-query',
        ),
    ).job_execute('reprocess_run')


def test_same_day_trade_correction_after_failed_snapshot_marks_pnl_stale(history_database, monkeypatch):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    adapter.payload_bytes = _PAYLOAD.replace(b'tradePrice="100"', b'tradePrice="200"')

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after corrected trade commit')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert any(row['symbol'] == 'TEST' and row['action'] == 'BUY' and Decimal(row['price']) == 200
               for row in report['activity'])
    assert Decimal(report['totals'][0]['realized_pnl']) == Decimal('481.2')
    assert report['stale'] is True
    assert report['provisional'] is True
    stock_lot = next(row for row in report['lots'] if row['conid'] == '101')
    assert stock_lot['unrealized_pnl'] is None


def test_failed_older_artifact_replay_marks_latest_snapshot_stale(history_database, monkeypatch):
    client, _, ids, engine = history_database
    harness = _harness(engine, account='HISTORY')
    orchestrator, adapter, _, _, service, _, _ = harness
    adapter.payload_bytes = _PAYLOAD.replace(b'tradePrice="100"', b'tradePrice="200"').replace(
        b'<FlexStatement reportDate="20260821">', b'<FlexStatement reportDate="20260822">',
    )
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert before['stale'] is False
    assert before['provisional'] is False
    assert Decimal(before['totals'][0]['realized_pnl']) == Decimal('81.2')

    def fail_snapshot(**kwargs):
        raise RuntimeError('replay failure after restoring older canonical trade')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert _history_replay(engine, harness).status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert any(row['symbol'] == 'TEST' and row['action'] == 'BUY' and Decimal(row['price']) == 100
               for row in report['activity'])
    assert Decimal(report['totals'][0]['realized_pnl']) == Decimal('81.2')
    assert report['stale'] is True
    assert report['provisional'] is True


def test_successful_replay_clears_stale_after_new_activity(history_database, monkeypatch):
    client, _, ids, engine = history_database
    harness = _harness(engine, account='HISTORY')
    orchestrator, adapter, _, _, service, _, _ = harness
    new_cashflow = (b'<CashTransaction transactionID="21" conid="101" symbol="TEST" assetCategory="STK" '
                    b'type="Dividends" amount="7" currency="USD" reportDate="20260821" />')
    adapter.payload_bytes = _PAYLOAD.replace(b'</CashTransactions>', new_cashflow+b'</CashTransactions>')
    build = service.ledger_snapshot_build_and_persist

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after new dividend commit')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    assert client.get(f"/reports/stock-history/{ids['101']}").json()['stale'] is True
    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', build)
    assert _history_replay(engine, harness).status == 'success'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert Decimal(report['totals'][0]['realized_pnl']) == Decimal('488.2')
    assert report['stale'] is False
    assert report['provisional'] is False
    stock_lot = next(row for row in report['lots'] if row['conid'] == '101')
    assert Decimal(stock_lot['unrealized_pnl']) == Decimal('178.8')


def test_existing_snapshots_require_rebuild_after_freshness_migration(history_database):
    client, _, ids, engine = history_database
    command.downgrade(Config('alembic.ini'), '20260908_10')
    command.upgrade(Config('alembic.ini'), 'head')
    # Clearing a review flag does not establish when the P&L was calculated.
    with engine.begin() as connection:
        connection.execute(text('UPDATE pnl_snapshot_daily SET provisional=false'))
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['stale'] is True
    assert report['provisional'] is True
    assert Decimal(report['totals'][0]['realized_pnl']) == Decimal('481.2')
    assert all(row['provisional'] for row in report['lots'])
    assert _history_replay(engine, _harness(engine, account='HISTORY')).status == 'success'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['stale'] is False
    assert report['provisional'] is False
    assert Decimal(report['totals'][0]['realized_pnl']) == Decimal('481.2')


@pytest.mark.parametrize('metadata_section', ['Trades', 'SecuritiesInfo'])
def test_failed_snapshot_keeps_committed_adjusted_option_in_stock_family(history_database, monkeypatch, metadata_section):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    metadata = b'underlyingConid="101" underlyingSymbol="TEST"'
    trade = (b'<Trade ibExecID="NEWOPT" transactionID="30" conid="106" symbol="ADJUSTED OPTION" '
             b'assetCategory="OPT" currency="USD" buySell="BUY" quantity="1" tradePrice="2" '
             b'multiplier="100" reportDate="20260821" dateTime="20260821;140000" ')
    payload = _PAYLOAD.replace(b'</Trades>', trade+(metadata if metadata_section == 'Trades' else b'')+b'/></Trades>')
    if metadata_section == 'SecuritiesInfo':
        payload = payload.replace(b'<SecuritiesInfo />', b'<SecuritiesInfo><SecurityInfo conid="106" '+metadata+b'/></SecuritiesInfo>')
    adapter.payload_bytes = payload

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after new option canonical commit')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert '106' in {row['conid'] for row in report['positions']}
    assert any(row['symbol'] == 'ADJUSTED OPTION' for row in report['activity'])
    assert report['provisional'] is True
    assert report['totals'][0]['realized_pnl'] is None


@pytest.mark.parametrize('selected_index', [0, 1, 2])
def test_duplicate_stock_symbols_do_not_share_symbol_only_options(selected_index):
    instruments = [
        {'instrument_id': uuid4(), 'conid': '101', 'asset_category': 'STK', 'symbol': 'TEST'},
        {'instrument_id': uuid4(), 'conid': '201', 'asset_category': 'STK', 'symbol': 'TEST'},
        {'instrument_id': uuid4(), 'conid': '102', 'asset_category': 'OPT', 'symbol': 'TEST 260918P00100000'},
        {'instrument_id': uuid4(), 'conid': '103', 'asset_category': 'OPT', 'symbol': 'TEST 260918C00100000',
         'underlying_conid': '101'},
    ]
    root, family = _stock_family(instruments[selected_index], instruments)
    assert root == instruments[selected_index]
    assert {row['conid'] for row in family} == [{'101', '103'}, {'201'}, {'102'}][selected_index]


@pytest.mark.parametrize('history_database,input_kind', [
    (_NO_CASH_PAYLOAD, 'valuation'), (_COMMISSION_FX_PAYLOAD, 'commission_fx'),
], indirect=['history_database'], ids=['valuation', 'commission-fx'])
def test_failed_valuation_or_fx_change_marks_pnl_stale_until_retry(history_database, monkeypatch, input_kind):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert before['provisional'] is False
    adapter.payload_bytes = (_NO_CASH_PAYLOAD.replace(b'markPrice="130"', b'markPrice="140"')
                             if input_kind == 'valuation' else _COMMISSION_FX_PAYLOAD.replace(b'rate="1.5"', b'rate="1.6"'))
    build = service.ledger_snapshot_build_and_persist

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after changed snapshot inputs')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['totals'] == before['totals']
    assert report['stale'] is True
    assert report['provisional'] is True
    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', build)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['stale'] is False
    assert report['provisional'] is False
    assert report['totals'] != before['totals']


@pytest.mark.parametrize('history_database', [_NO_CASH_PAYLOAD], indirect=True, ids=['no-cash'])
def test_retrying_old_valuation_artifact_after_replay_marks_pnl_stale(history_database, monkeypatch):
    client, _, ids, engine = history_database
    harness = _harness(engine, account='HISTORY')
    orchestrator, adapter, _, _, service, _, _ = harness
    adapter.payload_bytes = _NO_CASH_PAYLOAD.replace(b'markPrice="130"', b'markPrice="140"')
    build = service.ledger_snapshot_build_and_persist

    def fail_snapshot(**kwargs):
        raise RuntimeError('valuation attempt failed')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', build)
    assert _history_replay(engine, harness).status == 'success'
    assert client.get(f"/reports/stock-history/{ids['101']}").json()['stale'] is False
    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['stale'] is True
    assert report['provisional'] is True


@pytest.mark.parametrize('history_database', [_NO_CASH_PAYLOAD], indirect=True, ids=['no-cash'])
def test_failed_removal_from_nonempty_broker_positions_marks_pnl_stale(history_database, monkeypatch):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    stock_position = (b'<OpenPosition conid="101" symbol="TEST" assetCategory="STK" currency="USD"\n'
                      b'  position="6" markPrice="130" multiplier="1" reportDate="20260821" />')
    adapter.payload_bytes = _NO_CASH_PAYLOAD.replace(stock_position, b'')
    assert stock_position not in adapter.payload_bytes

    def fail_snapshot_stage(**kwargs):
        raise RuntimeError('failure before snapshot stage')

    monkeypatch.setattr(orchestrator, '_job_append_snapshot_stage_timeline', fail_snapshot_stage)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['stale'] is True
    assert report['provisional'] is True


@pytest.mark.parametrize('history_database', [_NO_CASH_PAYLOAD], indirect=True, ids=['no-cash'])
def test_unchanged_valuations_remain_fresh_after_metadata_only_import(history_database):
    client, _, ids, engine = history_database
    orchestrator, adapter, *_ = _harness(engine, account='HISTORY')
    adapter.payload_bytes = _NO_CASH_PAYLOAD.replace(
        b'<SecuritiesInfo />', b'<SecuritiesInfo><SecurityInfo conid="101" description="Updated description" /></SecuritiesInfo>',
    )
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['stale'] is False
    assert report['provisional'] is False


@pytest.mark.parametrize('history_database', [
    _PAYLOAD.replace(b'TEST  260918P00100000', b'ADJUSTED OPTION').replace(
        b'underlyingConid="101" underlyingSymbol="TEST"', b'',
    ),
], indirect=True, ids=['adjusted-option'])
def test_corrected_option_keeps_new_metadata_after_failed_snapshot(history_database, monkeypatch):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    adapter.payload_bytes = _PAYLOAD.replace(b'TEST  260918P00100000', b'ADJUSTED OPTION').replace(
        b'tradePrice="3"', b'tradePrice="4"',
    )

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after option correction commit')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert '102' in {row['conid'] for row in report['positions']}
    assert any(row['symbol'] == 'ADJUSTED OPTION' and Decimal(row['price']) == 4 for row in report['activity'])
    assert report['stale'] is True


@pytest.mark.parametrize('selected_index', [0, 1, 2])
def test_option_only_families_reject_ambiguous_underlying_symbols(selected_index):
    instruments = [
        {'instrument_id': uuid4(), 'conid': '1011', 'asset_category': 'OPT', 'symbol': 'TEST 260918P00100000',
         'underlying_conid': '101'},
        {'instrument_id': uuid4(), 'conid': '2011', 'asset_category': 'OPT', 'symbol': 'TEST 260918C00100000',
         'underlying_conid': '201'},
        {'instrument_id': uuid4(), 'conid': '3011', 'asset_category': 'OPT', 'symbol': 'TEST 261218P00100000'},
    ]
    root, family = _stock_family(instruments[selected_index], instruments)
    assert root == instruments[selected_index]
    assert family == [instruments[selected_index]]


@pytest.mark.parametrize('history_database', [
    _NO_CASH_PAYLOAD.replace(b'<FlexStatement reportDate="20260821">', b'<FlexStatement>'),
], indirect=True, ids=['no-statement-date'])
def test_valuation_freshness_without_statement_date_uses_processing_time(history_database, monkeypatch):
    client, _, ids, engine = history_database
    report = client.get(f"/reports/stock-history/{ids['101']}")
    assert report.status_code == 200
    assert report.json()['stale'] is False
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    adapter.payload_bytes = _NO_CASH_PAYLOAD.replace(b'<FlexStatement reportDate="20260821">', b'<FlexStatement>').replace(
        b'markPrice="130"', b'markPrice="140"',
    )

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after undated valuation attempt')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}")
    assert report.status_code == 200
    assert report.json()['stale'] is True


def test_successful_import_rebuilds_removed_broker_position(history_database):
    client, _, ids, engine = history_database
    orchestrator, adapter, *_ = _harness(engine, account='HISTORY')
    stock_position = (b'<OpenPosition conid="101" symbol="TEST" assetCategory="STK" currency="USD"\n'
                      b'  position="6" markPrice="130" multiplier="1" reportDate="20260821" />')
    adapter.payload_bytes = _PAYLOAD.replace(stock_position, b'')
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    stock = next(row for row in report['positions'] if row['conid'] == '101')
    assert Decimal(stock['position_qty']) == 0
    assert Decimal(stock['unrealized_pnl']) == 0
    assert report['stale'] is False


def test_cashflow_description_change_does_not_mark_pnl_stale(history_database, monkeypatch):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    adapter.payload_bytes = _PAYLOAD.replace(b'amount="5"', b'amount="5" description="Corrected description"')

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after non-accounting cashflow update')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert any(row['description'] == 'Corrected description' for row in report['activity'])
    assert report['totals'] == before['totals']
    assert report['stale'] is False
    assert report['provisional'] is False


@pytest.mark.parametrize('history_database', [
    _NO_CASH_PAYLOAD.replace(_NO_CASH_PAYLOAD.split(b'<Trades>')[1].split(b'</Trades>')[0], b''),
], indirect=True, ids=['broker-only'])
def test_successful_import_clears_omitted_holding_without_canonical_activity(history_database):
    client, _, ids, engine = history_database
    orchestrator, adapter, *_ = _harness(engine, account='HISTORY')
    payload = _NO_CASH_PAYLOAD.replace(_NO_CASH_PAYLOAD.split(b'<Trades>')[1].split(b'</Trades>')[0], b'')
    adapter.payload_bytes = payload.replace(payload.split(b'<OpenPositions>')[1].split(b'</OpenPositions>')[0], b'')
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert all(Decimal(row['position_qty']) == 0 for row in report['positions'])
    assert report['stale'] is False
    assert report['provisional'] is False


@pytest.mark.parametrize('history_database,changed_payload,expected_stale', [
    pytest.param(
        _SUPERSEDED_FX_PAYLOAD, _SUPERSEDED_FX_PAYLOAD.replace(b'rate="1.2"', b'rate="1.3"'), False,
        id='superseded-rate',
    ),
    pytest.param(
        _DIRECT_TRADE_FX_PAYLOAD, _DIRECT_TRADE_FX_PAYLOAD.replace(b'rate="1.2"', b'rate="1.3"'), False,
        id='closed-direct-trade-fx',
    ),
    pytest.param(
        _DIRECT_BROKER_FX_PAYLOAD, _DIRECT_BROKER_FX_PAYLOAD.replace(b'rate="1.2"', b'rate="1.3"'), False,
        id='open-direct-broker-fx',
    ),
    pytest.param(
        _BASE_CASHFLOW_FX_PAYLOAD, _BASE_CASHFLOW_FX_PAYLOAD.replace(b'rate="1.2"', b'rate="1.3"'), False,
        id='cashflow-amount-in-base',
    ),
    pytest.param(
        _SUPERSEDED_FX_PAYLOAD, _SUPERSEDED_FX_PAYLOAD.replace(b'rate="1.5"', b'rate="0"'), True,
        id='selected-rate-becomes-invalid',
    ),
    pytest.param(
        _PREVIOUS_FX_PAYLOAD, _PREVIOUS_FX_PAYLOAD.replace(
            b'</ConversionRates>', b'<ConversionRate fromCurrency="GBP" toCurrency="USD" '
            b'reportDate="20260820" rate="1.6" /></ConversionRates>',
        ), True, id='newly-eligible-rate',
    ),
    pytest.param(
        _COMMISSION_FX_PAYLOAD.replace(b'rate="1.5"', b''), _COMMISSION_FX_PAYLOAD, True,
        id='missing-rate-becomes-available',
    ),
    pytest.param(
        _PREVIOUS_FX_PAYLOAD, _PREVIOUS_FX_PAYLOAD.replace(
            b'</ConversionRates>', b'<ConversionRate fromCurrency="GBP" toCurrency="USD" '
            b'reportDate="20260820" rate="1.5" /></ConversionRates>',
        ), False, id='new-selection-same-effective-rate',
    ),
], indirect=['history_database'])
def test_fx_freshness_tracks_consumed_effective_rates(history_database, monkeypatch, changed_payload, expected_stale):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert before['stale'] is False
    adapter.payload_bytes = changed_payload
    build = service.ledger_snapshot_build_and_persist

    def fail_snapshot(**kwargs):
        raise RuntimeError('snapshot failure after FX-only canonical change')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    pending = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert pending['totals'] == before['totals']
    assert pending['stale'] is expected_stale
    if not expected_stale:
        assert pending['provisional'] is False

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', build)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    rebuilt = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert rebuilt['stale'] is False
    assert rebuilt['provisional'] is False
    assert (rebuilt['totals'] != before['totals']) is expected_stale


@pytest.mark.parametrize('history_database', [_NO_CASH_PAYLOAD], indirect=True, ids=['no-cash'])
@pytest.mark.parametrize('position_attributes', [
    pytest.param(b'position="6" markPrice="130" multiplier="1" description="Updated security description"', id='metadata'),
    pytest.param(b'position="6.0000" markPrice="130.0000" multiplier="1.00"', id='equivalent-numeric-format'),
])
def test_equivalent_broker_valuation_after_failed_snapshot_stays_fresh(history_database, monkeypatch, position_attributes):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert before['provisional'] is False
    adapter.payload_bytes = _NO_CASH_PAYLOAD.replace(
        b'position="6" markPrice="130" multiplier="1"', position_attributes,
    )

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after equivalent valuation import')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['totals'] == before['totals']
    assert report['stale'] is False
    assert report['provisional'] is False
    stock_lot = next(row for row in report['lots'] if row['conid'] == '101')
    assert Decimal(stock_lot['unrealized_pnl']) == Decimal('178.8')


@pytest.mark.parametrize('history_database', [_NO_CASH_PAYLOAD], indirect=True, ids=['no-cash'])
@pytest.mark.parametrize('attribute,column', [('cost', 'cost'), ('fifoPnlRealized', 'realized_pnl')])
def test_unused_broker_trade_figures_after_failed_snapshot_stay_fresh(history_database, monkeypatch, attribute, column):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert before['provisional'] is False
    adapter.payload_bytes = _NO_CASH_PAYLOAD.replace(
        b'tradePrice="100"', f'tradePrice="100" {attribute}="999"'.encode(),
    )

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after broker-only trade figure correction')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    with engine.connect() as connection:
        assert connection.scalar(text(f"SELECT {column} FROM event_trade_fill WHERE ib_exec_id='S1'")) == Decimal('999')
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['totals'] == before['totals']
    assert report['stale'] is False
    assert report['provisional'] is False


@pytest.mark.parametrize('history_database', [_NO_CASH_PAYLOAD], indirect=True, ids=['no-cash'])
def test_successful_trade_description_correction_appears_in_activity(history_database):
    client, _, ids, engine = history_database
    orchestrator, adapter, *_ = _harness(engine, account='HISTORY')
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    opening = next(row for row in before['activity'] if row['symbol'] == 'TEST' and row['action'] == 'BUY')
    adapter.payload_bytes = _NO_CASH_PAYLOAD.replace(
        b'tradePrice="100"', b'tradePrice="100" description="Corrected execution description"',
    )
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    corrected = next(row for row in report['activity'] if row['event_id'] == opening['event_id'])
    assert corrected['description'] == 'Corrected execution description'
    assert report['totals'] == before['totals']
    assert report['stale'] is False


@pytest.mark.parametrize('history_database', [
    _NO_CASH_PAYLOAD.replace(b'tradePrice="100"', b'tradePrice="100" description="Original execution description"'),
], indirect=True, ids=['original-description'])
def test_trade_description_migration_preserves_provenance_and_snapshot_freshness(history_database):
    client, _, ids, engine = history_database
    with engine.connect() as connection:
        original = connection.execute(text(
            "SELECT source_raw_record_id,ingestion_run_id,updated_at_utc FROM event_trade_fill WHERE ib_exec_id='S1'"
        )).one()
        calculated_at = connection.scalar(text(
            'SELECT calculated_at_utc FROM pnl_snapshot_daily WHERE instrument_id=:id'
        ), {'id': ids['101']})
    command.downgrade(Config('alembic.ini'), '20260910_13')
    command.upgrade(Config('alembic.ini'), 'head')
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT source_raw_record_id,ingestion_run_id,updated_at_utc FROM event_trade_fill WHERE ib_exec_id='S1'"
        )).one() == original
        assert connection.scalar(text(
            "SELECT description FROM event_trade_fill WHERE ib_exec_id='S1'"
        )) == 'Original execution description'
        assert connection.scalar(text(
            'SELECT calculated_at_utc FROM pnl_snapshot_daily WHERE instrument_id=:id'
        ), {'id': ids['101']}) == calculated_at
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['stale'] is False
    assert report['provisional'] is False
    assert any(row['description'] == 'Original execution description' for row in report['activity'])


@pytest.mark.parametrize('history_database', [_NO_CASH_PAYLOAD], indirect=True, ids=['no-cash'])
def test_failed_snapshot_after_trade_description_correction_preserves_freshness(history_database, monkeypatch):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    with engine.connect() as connection:
        original = connection.execute(text(
            "SELECT source_raw_record_id,ingestion_run_id,updated_at_utc FROM event_trade_fill WHERE ib_exec_id='S1'"
        )).one()
    adapter.payload_bytes = _NO_CASH_PAYLOAD.replace(
        b'tradePrice="100"', b'tradePrice="100" description="Corrected execution description"',
    )

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after description-only correction')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT source_raw_record_id,ingestion_run_id,updated_at_utc FROM event_trade_fill WHERE ib_exec_id='S1'"
        )).one() == original
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['totals'] == before['totals']
    assert report['stale'] is False
    assert report['provisional'] is False
    assert any(row['description'] == 'Corrected execution description' for row in report['activity'])


@pytest.mark.parametrize('history_database', [_NO_CASH_PAYLOAD], indirect=True, ids=['no-cash'])
def test_valuation_from_failed_canonical_mapping_does_not_mark_pnl_stale(history_database):
    client, _, ids, engine = history_database
    orchestrator, adapter, *_ = _harness(engine, account='HISTORY')
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert before['provisional'] is False
    adapter.payload_bytes = _NO_CASH_PAYLOAD.replace(b'markPrice="130"', b'markPrice="140"').replace(
        b'tradePrice="100"', b'tradePrice="not-a-number"',
    )
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT valuation_pending_at_utc FROM raw_artifact ORDER BY created_at_utc DESC LIMIT 1"
        )) is None
        assert connection.scalar(text("SELECT price FROM event_trade_fill WHERE ib_exec_id='S1'")) == Decimal('100')
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['totals'] == before['totals']
    assert report['stale'] is False
    assert report['provisional'] is False


@pytest.mark.parametrize('history_database,changed_payload,expected_stale', [
    pytest.param(
        _BASE_NET_CASH_PAYLOAD, _BASE_NET_CASH_PAYLOAD.replace(b'netCash="-1002"', b'netCash="-1003"'), False,
        id='base-currency',
    ),
    pytest.param(
        _DIRECT_NET_CASH_PAYLOAD, _DIRECT_NET_CASH_PAYLOAD.replace(b'netCash="-10.01"', b'netCash="-20.02"'), False,
        id='direct-fx-override',
    ),
    pytest.param(
        _RATIO_NET_CASH_PAYLOAD, _RATIO_NET_CASH_PAYLOAD.replace(b'netCash="-10.01"', b'netCash="-20.02"'), True,
        id='consumed-net-cash-ratio',
    ),
], indirect=['history_database'])
def test_net_cash_correction_only_invalidates_consumed_fx_ratio(history_database, monkeypatch, changed_payload, expected_stale):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert before['provisional'] is False
    adapter.payload_bytes = changed_payload
    build = service.ledger_snapshot_build_and_persist

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after net cash correction')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['totals'] == before['totals']
    assert report['stale'] is expected_stale
    assert report['provisional'] is expected_stale
    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', build)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    rebuilt = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert rebuilt['stale'] is False
    assert rebuilt['provisional'] is False
    assert (rebuilt['totals'] != before['totals']) is expected_stale


@pytest.mark.parametrize('history_database', [_CLOSED_BROKER_FX_PAYLOAD], indirect=True, ids=['closed-broker-position'])
@pytest.mark.parametrize('new_rate', ['1.3', ''], ids=['changed-rate', 'missing-rate'])
def test_closed_broker_position_does_not_depend_on_unused_valuation_fx(history_database, monkeypatch, new_rate):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert before['provisional'] is False
    assert Decimal(before['positions'][0]['position_qty']) == 0
    adapter.payload_bytes = _CLOSED_BROKER_FX_PAYLOAD.replace(b'rate="1.2"', f'rate="{new_rate}"'.encode())
    build = service.ledger_snapshot_build_and_persist

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after unused broker FX update')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['totals'] == before['totals']
    assert report['stale'] is False
    assert report['provisional'] is False
    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', build)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    rebuilt = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert rebuilt['totals'] == before['totals']
    assert rebuilt['stale'] is False
    assert rebuilt['provisional'] is False


@pytest.mark.parametrize('history_database', [_NO_CASH_PAYLOAD], indirect=True, ids=['reconciled-fifo'])
@pytest.mark.parametrize('attribute', ['costBasisMoney', 'fifoPnlUnrealized'])
def test_unused_broker_position_figures_do_not_invalidate_fifo_valuation(history_database, monkeypatch, attribute):
    client, _, ids, engine = history_database
    orchestrator, adapter, _, _, service, _, _ = _harness(engine, account='HISTORY')
    before = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert before['provisional'] is False
    adapter.payload_bytes = _NO_CASH_PAYLOAD.replace(b'markPrice="130"', f'markPrice="130" {attribute}="999"'.encode())
    build = service.ledger_snapshot_build_and_persist

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after unused broker valuation figure update')

    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
    assert orchestrator.job_execute('ingestion_run').status == 'failed'
    report = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert report['totals'] == before['totals']
    assert report['stale'] is False
    assert report['provisional'] is False
    monkeypatch.setattr(service, 'ledger_snapshot_build_and_persist', build)
    assert orchestrator.job_execute('ingestion_run').status == 'success'
    rebuilt = client.get(f"/reports/stock-history/{ids['101']}").json()
    assert rebuilt['totals'] == before['totals']
