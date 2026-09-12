"""Persisted movement lots, later sales, and historical projection protection."""
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.db import SQLAlchemyLedgerSnapshotService, SQLAlchemyPortfolioService
from app.db.corporate_action_evidence import action_evidence
from app.ledger import StockLedgerSnapshotService
from test_corporate_action_resolutions import _payload
import test_ingestion_integrity_regressions as ingestion_tests


database = ingestion_tests.database


def _resolve(database, basis=None, event=None):
    with database.begin() as connection:
        if event is None:
            event = connection.execute(text("SELECT event_corp_action_id FROM event_corp_action LIMIT 1")).scalar_one()
        evidence = action_evidence(connection, event)
        movement = evidence['movement']
        connection.execute(text(
            "INSERT INTO corporate_action_resolution(event_corp_action_id, source_signature, source_instrument_id, "
            "destination_instrument_id, report_date_local, quantity, cost_basis, currency, treatment, active, note) "
            "VALUES(:event, :signature, :source_instrument_id, :destination_instrument_id, :report_date_local, "
            ":quantity, :basis, :currency, :treatment, true, 'Verified regression fixture')"
        ), {**movement, 'event': event, 'signature': evidence['source_signature'], 'basis': basis,
            'treatment': 'distribution' if basis is not None else 'security_transfer'})
        connection.execute(text("UPDATE event_corp_action SET requires_manual=false WHERE event_corp_action_id=:id"), {'id': event})
    return event


def _rebuild(database, day, run_id=None, conids=None):
    service = StockLedgerSnapshotService(SQLAlchemyLedgerSnapshotService(database))
    return service.ledger_snapshot_build_and_persist(
        'INTEGRITY', str(run_id) if run_id else None, day, 'USD', affected_conids=conids,
    )


def test_transfer_persists_realized_attribution_fifo_origin_and_scoped_replay(database):
    harness = ingestion_tests._harness(database)
    payload = _payload().replace(b'<FlexStatement reportDate="20260821">', b'<FlexStatement reportDate="20260823">')
    payload = payload.replace(b'buySell="BUY" quantity="2"', b'buySell="BUY" quantity="3"').replace(
        b'ibCommission="1" commission="1"', b'ibCommission="0" commission="0"',
    ).replace(b'</Trades>',
        b'<Trade transactionID="BEFORE" ibExecID="BEFORE" conid="900001" symbol="SEED" assetCategory="STK" '
        b'buySell="SELL" quantity="1" tradePrice="120" currency="USD" reportDate="20260820" dateTime="20260820;130000" />'
        b'<Trade transactionID="AFTER" ibExecID="AFTER" conid="900002" symbol="NEXT" assetCategory="STK" '
        b'buySell="SELL" quantity="1" tradePrice="130" currency="USD" reportDate="20260822" dateTime="20260822;130000" /></Trades>',
    ).replace(b'reportDate="20260821" position="2"', b'reportDate="20260823" position="1"')
    harness[1].payload_bytes = payload
    assert harness[0].job_execute('ingestion_run').status == 'success'
    event = _resolve(database)
    with database.connect() as connection:
        run_id = connection.scalar(text('SELECT ingestion_run_id FROM ingestion_run LIMIT 1'))
        instrument_ids = dict(connection.execute(text('SELECT symbol,instrument_id FROM instrument')).all())
        opening_id = connection.scalar(text("SELECT event_trade_fill_id FROM event_trade_fill WHERE side='BUY'"))
    _rebuild(database, '2026-08-23', run_id, frozenset({'900002'}))
    with database.connect() as connection:
        snapshots = {row['symbol']: row for row in connection.execute(text(
            "SELECT i.symbol,s.* FROM pnl_snapshot_daily s JOIN instrument i USING(instrument_id) "
            "WHERE s.report_date_local='2026-08-23'"
        )).mappings()}
        lots = {row['symbol']: row for row in connection.execute(text(
            'SELECT i.symbol,l.* FROM position_lot l JOIN instrument i USING(instrument_id)'
        )).mappings()}
        assert connection.scalar(text('SELECT count(*) FROM event_trade_fill')) == 3
    assert snapshots['SEED']['position_qty'] == 0
    assert snapshots['SEED']['realized_pnl'] == 20
    assert snapshots['NEXT']['position_qty'] == 1
    assert snapshots['NEXT']['realized_pnl'] == 30
    assert snapshots['NEXT']['cost_basis'] == 100
    assert lots['SEED']['status'] == 'closed'
    assert lots['SEED']['realized_pnl_to_date'] == 20
    assert lots['NEXT']['open_event_trade_fill_id'] == opening_id
    assert lots['NEXT']['open_event_corp_action_id'] == event
    assert lots['NEXT']['opened_at_utc'] == lots['SEED']['opened_at_utc']
    report = SQLAlchemyPortfolioService(database).db_report_stock_history('INTEGRITY', instrument_ids['NEXT'])
    assert report['lots'][0]['remaining_quantity'] == 1
    assert report['lots'][0]['realized_pnl'] == 30
    _rebuild(database, '2026-08-20')
    with database.connect() as connection:
        historical = connection.execute(text(
            "SELECT position_qty,realized_pnl FROM pnl_snapshot_daily WHERE report_date_local='2026-08-20'"
        )).one()
        assert tuple(historical) == (2, 20)
        assert connection.scalar(text("SELECT remaining_quantity FROM position_lot WHERE instrument_id=:id AND status='open'"),
                                 {'id': instrument_ids['NEXT']}) == 1
    _rebuild(database, '2026-08-23', run_id)
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM position_lot')) == 2


@pytest.mark.parametrize('basis', ['0', '25'])
def test_distribution_lot_without_trade_is_visible_in_history_and_later_sale(database, basis):
    harness = ingestion_tests._harness(database)
    payload = _payload('SO').replace(b'<FlexStatement reportDate="20260821">', b'<FlexStatement reportDate="20260823">')
    payload = payload.replace(b'</Trades>',
        b'<Trade transactionID="AFTER" ibExecID="AFTER" conid="900002" symbol="NEXT" assetCategory="STK" '
        b'buySell="SELL" quantity="1" tradePrice="30" currency="USD" reportDate="20260822" dateTime="20260822;130000" /></Trades>',
    ).replace(b'reportDate="20260821" position="5"', b'reportDate="20260823" position="4"')
    harness[1].payload_bytes = payload
    assert harness[0].job_execute('ingestion_run').status == 'success'
    event = _resolve(database, basis)
    with database.connect() as connection:
        run_id = connection.scalar(text('SELECT ingestion_run_id FROM ingestion_run LIMIT 1'))
        destination = connection.scalar(text("SELECT instrument_id FROM instrument WHERE symbol='NEXT'"))
    _rebuild(database, '2026-08-23', run_id)
    report = SQLAlchemyPortfolioService(database).db_report_stock_history('INTEGRITY', destination)
    assert len(report['lots']) == 1
    lot = report['lots'][0]
    assert lot['open_event_trade_fill_id'] is None
    assert lot['open_event_corp_action_id'] == event
    assert lot['remaining_quantity'] == 4
    assert lot['realized_pnl'] == 30 - Decimal(basis) / 5
    assert lot['cost_basis_remaining'] == Decimal(basis) * 4 / 5
    with database.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM event_trade_fill WHERE instrument_id=:id"), {'id': destination}) == 1
    _rebuild(database, '2026-08-20')
    with database.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM pnl_snapshot_daily WHERE instrument_id=:id AND report_date_local='2026-08-20'"), {'id': destination}) == 0
        assert connection.scalar(text("SELECT remaining_quantity FROM position_lot WHERE instrument_id=:id"), {'id': destination}) == 4


def test_multiple_distribution_lots_survive_persisted_round_trip_without_identity_collision(database):
    harness = ingestion_tests._harness(database)
    legs = [
        ('FIRST', 'FIRST-IN', 'SO', '900001', 'OLD', '2', '20260818'),
        ('SECOND', 'SECOND-IN', 'SO', '900001', 'OLD', '3', '20260819'),
        ('OUTBOUND', 'OUTBOUND-OUT', 'IC', '900001', 'OLD', '-5', '20260820'),
        ('OUTBOUND', 'OUTBOUND-IN', 'IC', '900002', 'NEW', '5', '20260820'),
        ('RETURN', 'RETURN-OUT', 'IC', '900002', 'NEW', '-5', '20260821'),
        ('RETURN', 'RETURN-IN', 'IC', '900001', 'OLD', '5', '20260821'),
    ]
    actions = ''.join(
        f'<CorporateAction actionID="{action}" transactionID="{transaction}" type="{kind}" conid="{conid}" '
        f'symbol="{symbol}" quantity="{quantity}" reportDate="{day}" assetCategory="STK" '
        'currency="USD" amount="0" proceeds="0" description="Verified fixture movement" />'
        for action, transaction, kind, conid, symbol, quantity, day in legs
    )
    harness[1].payload_bytes = (
        '<FlexQueryResponse><FlexStatements count="1"><FlexStatement reportDate="20260821">'
        '<Trades /><CashTransactions /><ConversionRates /><SecuritiesInfo />'
        '<OpenPositions><OpenPosition conid="900001" symbol="OLD" assetCategory="STK" currency="USD" '
        'position="5" markPrice="30" multiplier="1" reportDate="20260821" />'
        '<OpenPosition conid="900002" symbol="NEW" assetCategory="STK" currency="USD" '
        'position="0" markPrice="30" multiplier="1" reportDate="20260821" /></OpenPositions>'
        f'<CorporateActions>{actions}</CorporateActions><AccountInformation accountId="U_TEST" currency="USD" />'
        '</FlexStatement></FlexStatements></FlexQueryResponse>'
    ).encode()
    assert harness[0].job_execute('ingestion_run').status == 'success'
    with database.connect() as connection:
        events = dict(connection.execute(text('SELECT action_id,event_corp_action_id FROM event_corp_action')).all())
        run_id = connection.scalar(text('SELECT ingestion_run_id FROM ingestion_run LIMIT 1'))
    for action, event in events.items():
        _resolve(database, {'FIRST': '20', 'SECOND': '60'}.get(action), event)
    _rebuild(database, '2026-08-21', run_id)
    with database.connect() as connection:
        lots = list(connection.execute(text('SELECT * FROM position_lot')).mappings())
    assert len(lots) == 6
    assert len({lot['position_lot_id'] for lot in lots}) == 6
    open_lots = [lot for lot in lots if lot['status'] == 'open']
    assert {lot['open_event_corp_action_id'] for lot in open_lots} == {events['FIRST'], events['SECOND']}
    assert sum(lot['remaining_quantity'] for lot in open_lots) == 5
    assert sum(lot['cost_basis_remaining'] for lot in open_lots) == 80
    _rebuild(database, '2026-08-21', run_id)
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM position_lot')) == 6


def test_equivalent_distribution_import_preserves_fifo_when_raw_uuid_order_reverses(database):
    import re
    with database.begin() as connection:
        connection.execute(text('CREATE SEQUENCE fixture_raw_identity'))
        connection.execute(text("ALTER TABLE raw_record ALTER COLUMN raw_record_id SET DEFAULT "
                                "lpad(to_hex(nextval('fixture_raw_identity')),32,'0')::uuid"))
    harness = ingestion_tests._harness(database)
    payload = _payload('SO').replace(b'actionID="MOVE"', b'actionID="FIRST"')
    first_leg = re.search(rb'<CorporateAction actionID="FIRST"[^>]*/>', payload).group()
    second_leg = first_leg.replace(b'actionID="FIRST"', b'actionID="SECOND"').replace(b'transactionID="IN"', b'transactionID="IN2"')
    payload = payload.replace(b'</CorporateActions>', second_leg + b'</CorporateActions>').replace(
        b'<FlexStatement reportDate="20260821">', b'<FlexStatement reportDate="20260823">',
    ).replace(b'reportDate="20260821" position="5"', b'reportDate="20260823" position="9"').replace(
        b'</Trades>', b'<Trade transactionID="SALE" ibExecID="SALE" conid="900002" symbol="NEXT" '
        b'assetCategory="STK" buySell="SELL" quantity="1" tradePrice="30" currency="USD" '
        b'reportDate="20260822" dateTime="20260822;130000" /></Trades>',
    )
    harness[1].payload_bytes = payload
    assert harness[0].job_execute('ingestion_run').status == 'success'
    with database.connect() as connection:
        events = dict(connection.execute(text('SELECT action_id,event_corp_action_id FROM event_corp_action')).all())
        run_id = connection.scalar(text('SELECT ingestion_run_id FROM ingestion_run LIMIT 1'))
    for action, event in events.items():
        _resolve(database, {'FIRST': '50', 'SECOND': '100'}[action], event)
    _rebuild(database, '2026-08-23', run_id)
    economics = text("SELECT p.position_qty,p.cost_basis,p.realized_pnl FROM pnl_snapshot_daily p "
                     "JOIN instrument i USING(instrument_id) WHERE i.symbol='NEXT' AND p.report_date_local='2026-08-23'")
    with database.begin() as connection:
        before = connection.execute(economics).one()
        original_sources = dict(connection.execute(text('SELECT action_id,source_raw_record_id FROM event_corp_action')).all())
        connection.execute(text("ALTER TABLE raw_record ALTER COLUMN raw_record_id SET DEFAULT "
                                "lpad(to_hex(1000000-nextval('fixture_raw_identity')),32,'0')::uuid"))
    harness[1].payload_bytes = payload.replace(b'<CorporateActions>', b'<CorporateActions>\n')
    assert harness[0].job_execute('ingestion_run').status == 'success'
    with database.connect() as connection:
        replay_sources = dict(connection.execute(text('SELECT action_id,source_raw_record_id FROM event_corp_action')).all())
        assert original_sources != replay_sources
        assert sorted(original_sources, key=original_sources.get) != sorted(replay_sources, key=replay_sources.get)
        assert connection.execute(economics).one() == before
        assert connection.scalar(text('SELECT count(*) FROM corporate_action_resolution WHERE active')) == 2
