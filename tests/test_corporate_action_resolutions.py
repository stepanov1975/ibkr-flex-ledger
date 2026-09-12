"""Actionable evidence and source-bound security movement corrections."""

from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text

from app.api.routers.corporate_actions import api_create_corporate_action_router
from app.db.portfolio import SQLAlchemyPortfolioService
import test_ingestion_integrity_regressions as ingestion_tests
from test_end_to_end_seeded import _SEEDED_PAYLOAD


database = ingestion_tests.database


def _payload(kind='IC'):
    if kind == 'IC':
        actions = (b'<CorporateAction actionID="MOVE" transactionID="OUT" conid="900001" symbol="SEED" '
                   b'assetCategory="STK" type="IC" quantity="-2" amount="0" proceeds="0" currency="USD" reportDate="20260821" '
                   b'description="SEED identifier change to NEXT" />'
                   b'<CorporateAction actionID="MOVE" transactionID="IN" conid="900002" symbol="NEXT" '
                   b'assetCategory="STK" type="IC" quantity="2" amount="0" proceeds="0" currency="USD" reportDate="20260821" '
                   b'description="SEED identifier change to NEXT" />')
        quantity, basis, value = b'2', b'201', b'220'
    else:
        actions = (b'<CorporateAction actionID="MOVE" transactionID="IN" conid="900002" symbol="NEXT" '
                   b'assetCategory="STK" type="SO" quantity="5" amount="0" proceeds="0" currency="USD" reportDate="20260821" '
                   b'description="SEED SPINOFF 1 FOR 60 (NEXT, CONTRA, US123)" costBasis="" />')
        quantity, basis, value = b'5', b'0', b'0'
    payload = _SEEDED_PAYLOAD.replace(b'<CorporateActions />', b'<CorporateActions>' + actions + b'</CorporateActions>')
    payload = payload.replace(b'reportDate="20260821" dateTime="20260821;120000"',
                              b'reportDate="20260820" dateTime="20260820;120000"')
    position = (b'<OpenPosition conid="900002" symbol="NEXT" assetCategory="STK" currency="USD" '
                b'reportDate="20260821" position="' + quantity + b'" markPrice="110" multiplier="1" '
                b'costBasisMoney="' + basis + b'" positionValue="' + value + b'" fxRateToBase="1" />')
    if kind == 'IC':
        start, end = payload.index(b'<OpenPositions>'), payload.index(b'</OpenPositions>') + len(b'</OpenPositions>')
        payload = payload[:start] + b'<OpenPositions>' + position + b'</OpenPositions>' + payload[end:]
    else:
        payload = payload.replace(b'</OpenPositions>', position + b'</OpenPositions>')
    return payload


def _case(database, kind='IC'):
    harness = ingestion_tests._harness(database)
    harness[1].payload_bytes = _payload(kind)
    assert harness[0].job_execute('ingestion_run').status == 'success'
    app = FastAPI()
    app.include_router(api_create_corporate_action_router(SQLAlchemyPortfolioService(database)))
    client = TestClient(app)
    return harness, client, client.get('/corporate-actions/cases').json()['items'][0]


def test_identifier_change_exposes_both_broker_legs_and_a_specific_resolution(database):
    _, _, case = _case(database)
    assert case['review_state'] == 'actionable'
    assert case['resolution_options'] == [{'type': 'security_transfer', 'label': 'Preview security transfer'}]
    assert {leg['symbol']: Decimal(leg['quantity']) for leg in case['broker_legs']} == {'SEED': -2, 'NEXT': 2}
    assert 'cost basis' in case['required_check']


def test_distribution_asks_for_basis_without_mistaking_blank_for_zero(database):
    _, _, case = _case(database, 'SO')
    assert case['review_state'] == 'actionable'
    assert case['resolution_options'] == [{'type': 'distribution', 'label': 'Enter distribution basis'}]
    assert case['broker_legs'][0]['cost_basis'] is None
    assert 'cost basis' in case['review_reason']


def test_incomplete_identifier_change_is_explained_as_unsupported(database):
    harness, client, _ = _case(database)
    import re
    harness[1].payload_bytes = re.sub(rb'<CorporateAction actionID="MOVE" transactionID="OUT"[^>]*/>', b'', _payload())
    assert harness[0].job_execute('ingestion_run').status == 'success'
    case = client.get('/corporate-actions/cases').json()['items'][0]
    assert case['review_state'] == 'unsupported'
    assert case['resolution_options'] == []
    assert 'two' in case['review_reason'].lower()


def _resolution_client(database):
    from app.db.corporate_action_correction import SQLAlchemySplitCorrectionService
    from app.db.corporate_action_resolution import SQLAlchemyCorporateActionResolutionService
    app = FastAPI()
    app.include_router(api_create_corporate_action_router(
        SQLAlchemyPortfolioService(database),
        correction_service=SQLAlchemySplitCorrectionService(database, 'INTEGRITY'),
        resolution_service=SQLAlchemyCorporateActionResolutionService(database, 'INTEGRITY'),
    ))
    return TestClient(app)


def _state(database):
    with database.connect() as connection:
        return {table: connection.execute(text(f'SELECT to_jsonb(t)::text FROM {table} t ORDER BY 1')).scalars().all()
                for table in ('raw_record', 'event_trade_fill', 'event_corp_action', 'corporate_action_manual_case',
                              'corporate_action_resolution', 'pnl_snapshot_daily', 'position_lot')}


@pytest.mark.parametrize('kind,body', [
    ('IC', {'treatment': 'security_transfer', 'note': 'Verified paired broker identifier change'}),
    ('SO', {'treatment': 'distribution', 'cost_basis': '0', 'note': 'Broker confirms credited entitlement with zero basis; no parent allocation'}),
    ('SO', {'treatment': 'distribution', 'cost_basis': '25', 'note': 'Broker confirms independently assigned basis; no parent allocation'}),
])
def test_resolution_preview_rolls_back_and_apply_rebuilds_with_replay(database, kind, body):
    harness, _, case = _case(database, kind)
    client = _resolution_client(database)
    base = f"/corporate-actions/cases/{case['case_id']}/resolution"
    before = _state(database)
    response = client.post(base + '/preview', json=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['applied'] is False
    assert 'event' in preview
    assert preview['event'] == {
        'event_corp_action_id': case['event_corp_action_id'], 'action_id': 'MOVE',
        'report_date_local': '2026-08-21', 'source_symbol': 'SEED' if kind == 'IC' else None,
        'destination_symbol': 'NEXT', 'quantity': '2' if kind == 'IC' else '5',
        'currency': 'USD', 'cost_basis': body.get('cost_basis'), 'note': body['note'],
    }
    assert _state(database) == before
    after_lots = [lot for lot in preview['lots_after'] if lot['symbol'] == 'NEXT' and Decimal(lot['remaining_quantity'])]
    assert sum(Decimal(lot['remaining_quantity']) for lot in after_lots) == (2 if kind == 'IC' else 5)
    assert sum(Decimal(lot['cost_basis_remaining']) for lot in after_lots) == (201 if kind == 'IC' else Decimal(body['cost_basis']))
    if kind == 'IC':
        assert after_lots[0]['opened_at_utc'].startswith('2026-08-20')
        source = next(row for row in preview['snapshots'] if row['symbol'] == 'SEED')
        assert Decimal(source['after']['realized_pnl']) == 0
    response = client.post(base + '/apply', json={**body, 'preview_token': preview['preview_token']})
    assert response.status_code == 200, response.text
    applied = _state(database)
    assert applied['raw_record'] == before['raw_record']
    assert applied['event_trade_fill'] == before['event_trade_fill']
    assert client.get('/corporate-actions/cases').json()['items'][0]['review_state'] == 'handled'
    with database.connect() as connection:
        period = connection.scalar(text('SELECT period_key FROM raw_artifact LIMIT 1'))
    assert ingestion_tests._replay(harness, period).status == 'success'
    assert client.get('/corporate-actions/cases').json()['items'][0]['review_state'] == 'handled'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT active FROM corporate_action_resolution')) is True
        assert connection.scalar(text("SELECT sum(l.remaining_quantity) FROM position_lot l JOIN instrument i USING(instrument_id) WHERE i.symbol='NEXT' AND l.status='open'")) == (2 if kind == 'IC' else 5)


def test_distribution_across_daily_imports_creates_one_lot_and_survives_replay(database):
    harness, _, case = _case(database, 'SO')
    for day in (b'20260822', b'20260823', b'20260823'):
        harness[1].payload_bytes = _payload('SO').replace(
            b'<FlexStatement reportDate="20260821">', b'<FlexStatement reportDate="' + day + b'">',
        ).replace(b'reportDate="20260821" position=', b'reportDate="' + day + b'" position=')
        assert harness[0].job_execute('ingestion_run').status == 'success'
    client = _resolution_client(database)
    cases = client.get('/corporate-actions/cases').json()['items']
    assert len(cases) == 1
    assert cases[0]['case_id'] == case['case_id']
    base = f"/corporate-actions/cases/{case['case_id']}/resolution"
    body = {'treatment': 'distribution', 'cost_basis': '25', 'note': 'Verified total basis for all five units'}
    before = _state(database)
    response = client.post(base + '/preview', json=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert _state(database) == before
    assert {row['report_date_local'] for row in preview['snapshots']} == {'2026-08-21', '2026-08-22', '2026-08-23'}
    for row in preview['snapshots']:
        assert Decimal(row['after']['position_qty']) == 5
        assert Decimal(row['after']['cost_basis']) == 25
    assert len(preview['lots_after']) == 1
    assert Decimal(preview['lots_after'][0]['remaining_quantity']) == 5
    assert Decimal(preview['lots_after'][0]['cost_basis_remaining']) == 25
    assert preview['lots_after'][0]['opened_at_utc'] == '2026-08-20 21:00:00+00:00'
    response = client.post(base + '/apply', json={**body, 'preview_token': preview['preview_token']})
    assert response.status_code == 200, response.text
    harness[1].payload_bytes = harness[1].payload_bytes.replace(b'20260823', b'20260824')
    assert harness[0].job_execute('ingestion_run').status == 'success'
    with database.connect() as connection:
        period = connection.scalar(text('SELECT period_key FROM raw_artifact ORDER BY period_key DESC LIMIT 1'))
    assert ingestion_tests._replay(harness, period).status == 'success'
    assert client.get('/corporate-actions/cases').json()['items'][0]['review_state'] == 'handled'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM event_corp_action')) == 1
        assert connection.scalar(text('SELECT count(*) FROM corporate_action_resolution WHERE active')) == 1
        lots = connection.execute(text(
            "SELECT l.remaining_quantity,l.cost_basis_remaining FROM position_lot l JOIN instrument i USING(instrument_id) "
            "WHERE i.symbol='NEXT' AND l.status='open'"
        )).all()
        assert lots == [(Decimal('5'), Decimal('25'))]


@pytest.mark.parametrize('body', [
    {'treatment': 'distribution', 'note': 'Verified'},
    {'treatment': 'distribution', 'cost_basis': '-1', 'note': 'Verified'},
    {'treatment': 'distribution', 'cost_basis': 'NaN', 'note': 'Verified'},
    {'treatment': 'distribution', 'cost_basis': '0', 'note': '   '},
    {'treatment': 'security_transfer', 'note': 'Wrong treatment'},
])
def test_invalid_resolution_never_mutates_accounting(database, body):
    _, _, case = _case(database, 'SO')
    client = _resolution_client(database)
    before = _state(database)
    response = client.post(f"/corporate-actions/cases/{case['case_id']}/resolution/preview", json=body)
    assert response.status_code in (400, 422), response.text
    assert _state(database) == before


def test_stale_preview_and_running_ingestion_leave_resolution_unapplied(database):
    harness, _, case = _case(database)
    client = _resolution_client(database)
    base = f"/corporate-actions/cases/{case['case_id']}/resolution"
    body = {'treatment': 'security_transfer', 'note': 'Verified paired broker transfer'}
    preview = client.post(base + '/preview', json=body).json()
    with database.begin() as connection:
        connection.execute(text('UPDATE event_trade_fill SET price=101'))
    before = _state(database)
    response = client.post(base + '/apply', json={**body, 'preview_token': preview['preview_token']})
    assert response.status_code == 409, response.text
    assert _state(database) == before
    with harness[6].db_ingestion_run_guard('INTEGRITY'):
        run = harness[6].db_ingestion_run_create_started('INTEGRITY', 'manual', '2026-08-21', 'test', None)
        assert client.post(base + '/preview', json=body).status_code == 409
        harness[6].db_ingestion_run_finalize(run.ingestion_run_id, 'failed', 'TEST', 'Complete', [])


def test_changed_outgoing_leg_invalidates_transfer_even_when_incoming_is_unchanged(database):
    harness, _, case = _case(database)
    client = _resolution_client(database)
    base = f"/corporate-actions/cases/{case['case_id']}/resolution"
    body = {'treatment': 'security_transfer', 'note': 'Verified paired transfer'}
    preview = client.post(base + '/preview', json=body).json()
    assert client.post(base + '/apply', json={**body, 'preview_token': preview['preview_token']}).status_code == 200
    harness[1].payload_bytes = _payload().replace(b'type="IC" quantity="-2"', b'type="IC" quantity="-3"')
    result = harness[0].job_execute('ingestion_run')
    with database.connect() as connection:
        failure = connection.execute(text("SELECT error_message,diagnostics FROM ingestion_run WHERE status='failed'")).all()
    assert result.status == 'success', failure
    case = client.get('/corporate-actions/cases').json()['items'][0]
    assert case['review_state'] == 'unsupported'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT active FROM corporate_action_resolution')) is False
        assert connection.scalar(text("SELECT count(*) FROM position_lot l JOIN instrument i USING(instrument_id) WHERE i.symbol='NEXT' AND l.status='open'")) == 0
        assert connection.scalar(text("SELECT bool_and(provisional) FROM pnl_snapshot_daily")) is True
    # Restored source reactivates the original treatment and retains evidence.
    harness[1].payload_bytes = _payload().replace(b'<CorporateActions>', b'\n<CorporateActions>')
    assert harness[0].job_execute('ingestion_run').status == 'success'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT active FROM corporate_action_resolution')) is True
        assert connection.scalar(text('SELECT note FROM corporate_action_resolution')) == body['note']


def test_partial_holding_transfer_is_rejected_without_changes(database):
    harness, _, case = _case(database)
    with database.begin() as connection:
        connection.execute(text('UPDATE event_trade_fill SET quantity=3'))
    before = _state(database)
    response = _resolution_client(database).post(f"/corporate-actions/cases/{case['case_id']}/resolution/preview",
                                               json={'treatment': 'security_transfer', 'note': 'Verified'})
    assert response.status_code == 400, response.text
    assert _state(database) == before


def test_late_historical_trades_rebuild_downstream_transfer_without_action_rows(database):
    harness, _, case = _case(database)
    client = _resolution_client(database)
    base = f"/corporate-actions/cases/{case['case_id']}/resolution"
    body = {'treatment': 'security_transfer', 'note': 'Verified'}
    preview = client.post(base + '/preview', json=body).json()
    assert client.post(base + '/apply', json={**body, 'preview_token': preview['preview_token']}).status_code == 200
    harness[1].payload_bytes = _SEEDED_PAYLOAD.replace(b'20260821', b'20260820').replace(
        b'</Trades>',
        b'<Trade transactionID="LATESELL" ibExecID="LATESELL" conid="900001" symbol="SEED" assetCategory="STK" '
        b'buySell="SELL" quantity="2" tradePrice="120" currency="USD" reportDate="20260820" dateTime="20260820;130000" />'
        b'<Trade transactionID="LATEBUY" ibExecID="LATEBUY" conid="900001" symbol="SEED" assetCategory="STK" '
        b'buySell="BUY" quantity="2" tradePrice="200" currency="USD" reportDate="20260820" dateTime="20260820;140000" /></Trades>',
    )
    result = harness[0].job_execute('ingestion_run')
    with database.connect() as connection:
        failure = connection.execute(text("SELECT error_message,diagnostics FROM ingestion_run WHERE status='failed'")).all()
    assert result.status == 'success', failure
    with database.connect() as connection:
        destination = connection.execute(text("SELECT p.* FROM pnl_snapshot_daily p JOIN instrument i USING(instrument_id) "
                                               "WHERE i.symbol='NEXT' AND p.report_date_local='2026-08-21'")).mappings().one()
        assert destination['cost_basis'] == 400
        assert connection.scalar(text("SELECT l.cost_basis_remaining FROM position_lot l JOIN instrument i USING(instrument_id) "
                                      "WHERE i.symbol='NEXT' AND l.status='open'")) == 400


def test_resolution_preserves_each_snapshot_valuation_context(database):
    harness, _, case = _case(database)
    harness[1].payload_bytes = _payload().replace(b'markPrice="110"', b'markPrice="120"')
    assert harness[0].job_execute('ingestion_run').status == 'success'
    with database.begin() as connection:
        low, high = sorted(connection.execute(text('SELECT ingestion_run_id FROM ingestion_run')).scalars())
        connection.execute(text("UPDATE raw_record SET source_payload=jsonb_set(source_payload,'{markPrice}', "
                                "to_jsonb(CASE WHEN ingestion_run_id=:low THEN '120'::text ELSE '110'::text END)) "
                                "WHERE section_name='OpenPositions'"), {'low': low})
        connection.execute(text("UPDATE pnl_snapshot_daily p SET ingestion_run_id=CASE WHEN i.symbol='NEXT' THEN :low ELSE :high END, "
                                "unrealized_pnl=CASE WHEN i.symbol='NEXT' THEN 39 ELSE p.unrealized_pnl END "
                                "FROM instrument i WHERE i.instrument_id=p.instrument_id"), {'low': low, 'high': high})
    response = _resolution_client(database).post(f"/corporate-actions/cases/{case['case_id']}/resolution/preview",
                                                json={'treatment': 'security_transfer', 'note': 'Verified'})
    assert response.status_code == 200, response.text
    destination = next(row for row in response.json()['snapshots'] if row['symbol'] == 'NEXT')
    assert Decimal(destination['after']['unrealized_pnl']) == 39


def test_new_historical_quantity_reopens_ineligible_transfer_without_blocking_import(database):
    harness, _, case = _case(database)
    client = _resolution_client(database)
    base = f"/corporate-actions/cases/{case['case_id']}/resolution"
    body = {'treatment': 'security_transfer', 'note': 'Verified'}
    preview = client.post(base + '/preview', json=body).json()
    assert client.post(base + '/apply', json={**body, 'preview_token': preview['preview_token']}).status_code == 200
    harness[1].payload_bytes = _SEEDED_PAYLOAD.replace(b'20260821', b'20260820').replace(
        b'</Trades>', b'<Trade transactionID="MISSINGBUY" ibExecID="MISSINGBUY" conid="900001" symbol="SEED" '
        b'assetCategory="STK" buySell="BUY" quantity="1" tradePrice="100" currency="USD" '
        b'reportDate="20260820" dateTime="20260820;140000" /></Trades>',
    )
    assert harness[0].job_execute('ingestion_run').status == 'success'
    reopened = client.get('/corporate-actions/cases').json()['items'][0]
    assert reopened['review_state'] == 'unsupported'
    assert 'full long position' in reopened['review_reason']
    with database.connect() as connection:
        assert connection.scalar(text('SELECT active FROM corporate_action_resolution')) is False
        assert connection.scalar(text("SELECT count(*) FROM event_trade_fill")) == 2
        assert connection.scalar(text("SELECT count(*) FROM position_lot l JOIN instrument i USING(instrument_id) "
                                      "WHERE i.symbol='NEXT' AND l.status='open'")) == 0


def test_new_upstream_split_reopens_ineligible_transfer_without_blocking_import(database):
    harness, _, case = _case(database)
    client = _resolution_client(database)
    base = f"/corporate-actions/cases/{case['case_id']}/resolution"
    body = {'treatment': 'security_transfer', 'note': 'Verified'}
    preview = client.post(base + '/preview', json=body).json()
    assert client.post(base + '/apply', json={**body, 'preview_token': preview['preview_token']}).status_code == 200
    # An otherwise unchanged statement supplies only a newly discovered split.
    import re
    harness[1].payload_bytes = re.sub(rb'<CorporateActions>.*?</CorporateActions>',
        b'<CorporateActions><CorporateAction actionID="EARLIERSPLIT" transactionID="EARLIERSPLIT" '
        b'conid="900001" symbol="SEED" assetCategory="STK" type="FS" ratio="2" currency="USD" '
        b'reportDate="20260821" /></CorporateActions>', _payload())
    result = harness[0].job_execute('ingestion_run')
    with database.connect() as connection:
        errors = connection.execute(text("SELECT error_message FROM ingestion_run WHERE status='failed'")).scalars().all()
    assert result.status == 'success', errors
    with database.connect() as connection:
        assert connection.scalar(text('SELECT active FROM corporate_action_resolution')) is False
        assert connection.scalar(text("SELECT requires_manual FROM event_corp_action WHERE action_id='MOVE'")) is True


@pytest.mark.parametrize('metadata', [b'assetCategory="OPT" currency="USD"', b'assetCategory="STK" currency="EUR"'])
def test_instrument_only_import_rechecks_and_restores_saved_resolution(database, metadata):
    harness, _, case = _case(database)
    client = _resolution_client(database)
    base = f"/corporate-actions/cases/{case['case_id']}/resolution"
    body = {'treatment': 'security_transfer', 'note': 'Verified'}
    preview = client.post(base + '/preview', json=body).json()
    assert client.post(base + '/apply', json={**body, 'preview_token': preview['preview_token']}).status_code == 200
    import re
    metadata_report = re.sub(rb'<CorporateActions>.*?</CorporateActions>', b'<CorporateActions />', _payload())
    harness[1].payload_bytes = metadata_report.replace(
        b'<OpenPosition conid="900002" symbol="NEXT" assetCategory="STK" currency="USD"',
        b'<OpenPosition conid="900002" symbol="NEXT" ' + metadata,
    )
    result = harness[0].job_execute('ingestion_run')
    with database.connect() as connection:
        errors = connection.execute(text("SELECT error_message FROM ingestion_run WHERE status='failed'")).scalars().all()
    assert result.status == 'success', errors
    with database.connect() as connection:
        assert connection.scalar(text('SELECT active FROM corporate_action_resolution')) is False
        assert connection.scalar(text("SELECT requires_manual FROM event_corp_action WHERE action_id='MOVE'")) is True
    assert client.get('/corporate-actions/cases').json()['items'][0]['review_state'] == 'unsupported'
    harness[1].payload_bytes = metadata_report
    assert harness[0].job_execute('ingestion_run').status == 'success'
    with database.connect() as connection:
        assert connection.scalar(text('SELECT active FROM corporate_action_resolution')) is True
        assert connection.scalar(text("SELECT l.cost_basis_remaining FROM position_lot l JOIN instrument i USING(instrument_id) "
                                      "WHERE i.symbol='NEXT' AND l.status='open'")) == 201


@pytest.mark.parametrize('reactivate', [False, True])
def test_manual_split_correction_rechecks_downstream_transfer_atomically(database, reactivate):
    harness, _, case = _case(database)
    client = _resolution_client(database)
    base = f"/corporate-actions/cases/{case['case_id']}/resolution"
    body = {'treatment': 'security_transfer', 'note': 'Verified'}
    preview = client.post(base + '/preview', json=body).json()
    assert client.post(base + '/apply', json={**body, 'preview_token': preview['preview_token']}).status_code == 200
    split = (b'<CorporateAction actionID="MANUAL" transactionID="MANUAL" conid="900001" symbol="SEED" '
             b'assetCategory="STK" type="FS" currency="USD" reportDate="20260821" />')
    if reactivate:
        split = split.replace(b'type="FS"', b'type="RS"') + split.replace(
            b'actionID="MANUAL" transactionID="MANUAL"', b'actionID="AUTO" transactionID="AUTO" ratio="2"',
        )
    import re
    harness[1].payload_bytes = re.sub(rb'<CorporateActions>.*?</CorporateActions>',
                                     b'<CorporateActions>' + split + b'</CorporateActions>', _payload())
    assert harness[0].job_execute('ingestion_run').status == 'success'
    cases = client.get('/corporate-actions/cases').json()['items']
    split_case = next(item for item in cases if item['can_correct_split'])
    split_base = f"/corporate-actions/cases/{split_case['case_id']}/split"
    ratio = {'new_shares': '1' if reactivate else '2', 'old_shares': '2' if reactivate else '1', 'note': 'Verified split notice'}
    before = _state(database)
    response = client.post(split_base + '/preview', json=ratio)
    assert response.status_code == 200, response.text
    assert _state(database) == before
    response = client.post(split_base + '/apply', json={**ratio, 'preview_token': response.json()['preview_token']})
    assert response.status_code == 200, response.text
    with database.connect() as connection:
        assert connection.scalar(text('SELECT active FROM corporate_action_resolution')) is reactivate
        assert connection.scalar(text("SELECT requires_manual FROM event_corp_action WHERE action_id='MOVE'")) is not reactivate
        assert connection.scalar(text("SELECT status FROM corporate_action_manual_case c JOIN event_corp_action e USING(event_corp_action_id) "
                                      "WHERE e.action_id='MANUAL'")) == 'resolved'
        positions = dict(connection.execute(text("SELECT i.symbol,p.position_qty FROM pnl_snapshot_daily p JOIN instrument i USING(instrument_id) "
                                                "WHERE p.report_date_local='2026-08-21'")).all())
        # Broker-reported quantities remain authoritative; mismatched FIFO stays provisional.
        assert positions == {'SEED': 0, 'NEXT': 2}
        lots = dict(connection.execute(text("SELECT i.symbol,sum(l.remaining_quantity) FROM position_lot l JOIN instrument i USING(instrument_id) "
                                           "WHERE l.status='open' GROUP BY i.symbol")).all())
        assert lots == ({'NEXT': 2} if reactivate else {'SEED': 4})
        assert connection.scalar(text("SELECT p.provisional FROM pnl_snapshot_daily p JOIN instrument i USING(instrument_id) "
                                      "WHERE i.symbol='NEXT' AND p.report_date_local='2026-08-21'")) is not reactivate
