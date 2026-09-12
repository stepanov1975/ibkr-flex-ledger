"""Read complete broker action legs and describe supported accounting choices."""

from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.domain.flex_parsing import domain_flex_normalize_optional_text


def action_evidence(connection: Connection, event_id: UUID) -> dict[str, Any]:
    event = connection.execute(text(
        "SELECT e.*, r.raw_artifact_id FROM event_corp_action e "
        "JOIN raw_record r ON r.raw_record_id=e.source_raw_record_id WHERE e.event_corp_action_id=:id"
    ), {'id': event_id}).mappings().one()
    rows = connection.execute(text(
        "SELECT DISTINCT ON (r.source_payload::text) r.source_payload::text AS exact_payload, "
        "r.source_payload, i.instrument_id, i.symbol AS instrument_symbol, i.currency AS instrument_currency, i.asset_category "
        "FROM raw_record r LEFT JOIN instrument i "
        "ON i.account_id=r.account_id AND i.conid=r.source_payload->>'conid' "
        "WHERE r.account_id=:account_id AND r.section_name='CorporateActions' AND "
        "((CAST(:action_id AS text) IS NOT NULL AND r.source_payload->>'actionID'=:action_id "
        "AND r.raw_artifact_id=:artifact_id) OR r.raw_record_id=:source_id) "
        "ORDER BY r.source_payload::text, r.raw_record_id"
    ), {'account_id': event['account_id'], 'action_id': event['action_id'],
        'artifact_id': event['raw_artifact_id'], 'source_id': event['source_raw_record_id']}).mappings().all()
    legs = []
    for row in rows:
        payload = row['source_payload']
        legs.append({
            'symbol': domain_flex_normalize_optional_text(payload.get('symbol')) or row['instrument_symbol'] or payload.get('conid'),
            'conid': payload.get('conid'),
            'quantity': payload.get('quantity'), 'currency': payload.get('currency'),
            'cost_basis': payload.get('costBasis') or None,
            'report_date_local': _date(payload.get('reportDate')),
            'description': payload.get('description'), 'instrument_id': str(row['instrument_id']) if row['instrument_id'] else None,
        })
    signature = hashlib.sha256(json.dumps({
        'action_id': event['action_id'], 'date': str(event['report_date_local']), 'type': event['reorg_code'],
        'legs': [row['exact_payload'] for row in rows],
        'identities': [(str(row['instrument_id']), row['instrument_currency'], row['asset_category']) for row in rows],
    }, sort_keys=True).encode()).hexdigest()
    result: dict[str, Any] = {'broker_legs': legs, 'resolution_options': [], 'source_signature': signature}
    kind = event['reorg_code']
    if kind not in {'IC', 'SPINOFF'}:
        return result
    result['review_reason'] = ('An identifier change needs two matched broker legs with equal outgoing and incoming quantities.'
                               if kind == 'IC' else 'The credited security and its cost basis must be verified before recording this distribution.')
    result['required_check'] = ('Import both sides of the broker action, with matching date and currency and no cash payment.'
                                if kind == 'IC' else 'Import a single positive distribution leg and verify its security and total cost basis.')
    if not event['action_id'] or not rows or any(
        row['instrument_id'] is None or row['asset_category'] != 'STK'
        or row['source_payload'].get('currency') != row['instrument_currency']
        or _date(row['source_payload'].get('reportDate')) != str(event['report_date_local'])
        or str(row['source_payload'].get('type', '')).upper() not in ({'IC'} if kind == 'IC' else {'SO', 'SPINOFF'})
        or any(_number(row['source_payload'].get(field, '0')) != 0 for field in ('amount', 'proceeds'))
        or _number(row['source_payload'].get('multiplier', '1')) != 1
        for row in rows
    ):
        return result
    quantities = [_number(leg['quantity']) for leg in legs]
    if any(q is None or q == 0 for q in quantities):
        return result
    if kind == 'IC':
        if len(legs) != 2 or Decimal(legs[0]['quantity']) != -Decimal(legs[1]['quantity']) or legs[0]['conid'] == legs[1]['conid'] or legs[0]['currency'] != legs[1]['currency']:
            return result
        outgoing = next(leg for leg in legs if Decimal(leg['quantity']) < 0)
        incoming = next(leg for leg in legs if Decimal(leg['quantity']) > 0)
        result.update(review_reason=f"The broker records {outgoing['quantity']} {outgoing['symbol']} and +{incoming['quantity']} {incoming['symbol']}. The quantity matches; the holdings still need to be linked.",
                      required_check='Confirm the full holding moves to the new security with its existing cost basis and acquisition dates, then preview the transfer.',
                      resolution_options=[{'type': 'security_transfer', 'label': 'Preview security transfer'}])
    else:
        if len(legs) != 1 or Decimal(legs[0]['quantity']) <= 0:
            return result
        outgoing, incoming = None, legs[0]
        result.update(review_reason=f"The broker credits {incoming['quantity']} {incoming['symbol']}. The total cost basis and treatment of the credited security need confirmation; a blank basis is not zero.",
                      required_check='Enter the verified total cost basis of the credited units and record the broker evidence. This records a separate security and does not move cost basis from the parent; parent allocations need accounting support.',
                      resolution_options=[{'type': 'distribution', 'label': 'Enter distribution basis'}])
    result['movement'] = {
        'source_instrument_id': outgoing['instrument_id'] if outgoing else None,
        'destination_instrument_id': incoming['instrument_id'], 'quantity': incoming['quantity'],
        'currency': incoming['currency'], 'report_date_local': str(event['report_date_local']),
    }
    blocked_reason = connection.scalar(text('SELECT invalidated_reason FROM corporate_action_resolution WHERE event_corp_action_id=:id AND NOT active'),
                                       {'id': event_id})
    if blocked_reason:
        result.update(resolution_options=[], review_reason=f'The previously approved treatment no longer fits the imported holdings: {blocked_reason}',
                      required_check='Import the missing or corrected trade history. The app will recheck the saved treatment; partial or short-position transfers need accounting support.')
    return result


def _number(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def _date(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None
