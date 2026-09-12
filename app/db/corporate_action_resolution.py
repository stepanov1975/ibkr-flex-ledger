"""Source-bound security movement resolutions and atomic accounting previews."""

from decimal import Decimal
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from app.db.corporate_action_correction import SQLAlchemySplitCorrectionService, SplitCorrectionConflict
from app.db.corporate_action_evidence import action_evidence
from app.db.interfaces import LedgerSecurityMovementRecord
from app.db.ledger_snapshot import SQLAlchemyLedgerSnapshotService
from app.ledger import StockLedgerSnapshotService
from app.ledger.fifo_engine import FifoSecurityMovementError


def read_security_movements(connection: Connection, account_id: str, through_report_date_local: str) -> list[LedgerSecurityMovementRecord]:
    rows = connection.execute(text(
        "SELECT r.*, e.source_raw_record_id FROM corporate_action_resolution r "
        "JOIN event_corp_action e USING(event_corp_action_id) "
        "WHERE e.account_id=:account_id AND r.active AND NOT e.requires_manual "
        "AND r.report_date_local<=CAST(:day AS date) ORDER BY r.report_date_local, r.event_corp_action_id"
    ), {'account_id': account_id, 'day': through_report_date_local}).mappings().all()
    result = []
    for row in rows:
        if action_evidence(connection, row['event_corp_action_id'])['source_signature'] != row['source_signature']:
            raise ValueError('The approved corporate-action evidence changed. Reprocess the broker statement before rebuilding accounting.')
        result.append(LedgerSecurityMovementRecord(
            event_corp_action_id=row['event_corp_action_id'], source_raw_record_id=row['source_raw_record_id'],
            source_instrument_id=row['source_instrument_id'], destination_instrument_id=row['destination_instrument_id'],
            report_date_local=row['report_date_local'], quantity=str(row['quantity']),
            cost_basis=None if row['cost_basis'] is None else str(row['cost_basis']), currency=row['currency'],
        ))
    return result


class SQLAlchemyCorporateActionResolutionService:
    def __init__(self, engine: Engine, account_id: str):
        self._engine = engine
        self._account_id = account_id

    def preview_or_apply(self, case_id: UUID, treatment: str, note: str,
                         cost_basis: Decimal | None = None, preview_token: str | None = None) -> dict[str, Any]:
        if not note.strip():
            raise ValueError('Record the broker evidence supporting this treatment.')
        if treatment not in {'security_transfer', 'distribution'}:
            raise ValueError('Unsupported corporate-action treatment.')
        if treatment == 'distribution' and (cost_basis is None or not cost_basis.is_finite() or cost_basis < 0):
            raise ValueError('Enter an explicit, nonnegative total cost basis; a blank basis is not zero.')
        if treatment == 'security_transfer' and cost_basis is not None:
            raise ValueError('A security transfer carries existing lot basis; do not enter a replacement basis.')
        with self._engine.connect() as connection, connection.begin() as transaction:
            SQLAlchemySplitCorrectionService(self._engine, self._account_id)._lock_account(connection)
            case = connection.execute(text(
                "SELECT c.*, e.account_id, e.requires_manual, e.source_raw_record_id, e.report_date_local "
                "FROM corporate_action_manual_case c JOIN event_corp_action e USING(event_corp_action_id) "
                "WHERE c.case_id=:id AND e.account_id=:account_id FOR UPDATE OF c, e"
            ), {'id': case_id, 'account_id': self._account_id}).mappings().one_or_none()
            if case is None:
                raise LookupError('Corporate-action case not found.')
            if not case['requires_manual']:
                raise SplitCorrectionConflict('This action is already handled. Refresh the queue.')
            evidence = action_evidence(connection, case['event_corp_action_id'])
            if treatment not in {option['type'] for option in evidence['resolution_options']}:
                raise ValueError(evidence.get('review_reason', 'This broker action does not support the requested treatment.'))
            movement = evidence['movement']
            instrument_ids = {value for key, value in movement.items() if key.endswith('instrument_id') and value}
            instrument_ids = connected_instruments(connection, self._account_id, instrument_ids)
            params = {'account_id': self._account_id, 'ids': sorted(instrument_ids)}
            before = _snapshots(connection, params)
            if not before or max(row['report_date_local'] for row in before) < case['report_date_local']:
                raise SplitCorrectionConflict('No affected snapshots are available. Import the broker statement first.')
            latest_date = max(row['report_date_local'] for row in before)
            for table in ('event_trade_fill', 'event_cashflow', 'event_corp_action'):
                if connection.scalar(text(
                    f"SELECT EXISTS(SELECT 1 FROM {table} WHERE account_id=:account_id "
                    "AND instrument_id=ANY(CAST(:ids AS uuid[])) AND report_date_local>:latest)"
                ), {**params, 'latest': latest_date}):
                    raise SplitCorrectionConflict('Newer activity has no snapshot. Reprocess the failed ingestion first.')
            inputs = _accounting_inputs(connection, self._account_id)
            lots_before = _lots(connection, params)
            connection.execute(text(
                "INSERT INTO corporate_action_resolution(event_corp_action_id,source_signature,source_instrument_id, "
                "destination_instrument_id,report_date_local,quantity,cost_basis,currency,treatment,active,note) "
                "VALUES(:event_id,:signature,CAST(:source_instrument_id AS uuid),CAST(:destination_instrument_id AS uuid), "
                "CAST(:report_date_local AS date),:quantity,:cost_basis,:currency,:treatment,true,:note) "
                "ON CONFLICT(event_corp_action_id) DO UPDATE SET source_signature=EXCLUDED.source_signature, "
                "source_instrument_id=EXCLUDED.source_instrument_id,destination_instrument_id=EXCLUDED.destination_instrument_id, "
                "report_date_local=EXCLUDED.report_date_local,quantity=EXCLUDED.quantity,cost_basis=EXCLUDED.cost_basis, "
                "currency=EXCLUDED.currency,treatment=EXCLUDED.treatment,active=true,note=EXCLUDED.note,invalidated_reason=NULL,updated_at_utc=now()"
            ), {**movement, 'event_id': case['event_corp_action_id'], 'signature': evidence['source_signature'],
                'quantity': Decimal(movement['quantity']), 'cost_basis': cost_basis, 'treatment': treatment, 'note': note.strip()})
            connection.execute(text(
                "UPDATE corporate_action_manual_case SET status='resolved',resolution_note=:note, "
                "resolution_source_raw_record_id=:source_id,resolution_report_date_local=:day, "
                "resolved_at_utc=now(),updated_at_utc=now() WHERE case_id=:id"
            ), {'id': case_id, 'note': note.strip(), 'source_id': case['source_raw_record_id'], 'day': case['report_date_local']})
            connection.execute(text('UPDATE event_corp_action SET requires_manual=false,provisional=false WHERE event_corp_action_id=:id'),
                               {'id': case['event_corp_action_id']})
            rebuild_snapshots(connection, self._engine, self._account_id, instrument_ids, before)
            after = _snapshots(connection, params)
            old_by_key = {(row['instrument_id'], row['report_date_local'], row['currency']): row for row in before}
            fields = ('position_qty', 'cost_basis', 'realized_pnl', 'unrealized_pnl', 'total_pnl', 'provisional')
            snapshots = []
            for row in after:
                old = old_by_key.get((row['instrument_id'], row['report_date_local'], row['currency']), {})
                snapshots.append({'symbol': row['symbol'], 'report_date_local': str(row['report_date_local']),
                                  'currency': row['currency'], 'before': {field: old.get(field) for field in fields},
                                  'after': {field: row[field] for field in fields}})
            result = {'case_id': str(case_id), 'treatment': treatment,
                      'summary': ('Transfer the matched holding with its existing FIFO basis and acquisition dates.' if treatment == 'security_transfer'
                                  else f"Record {movement['quantity']} credited units with total cost basis {cost_basis} {movement['currency']}; parent basis stays unchanged."),
                      'snapshots': snapshots, 'lots_before': lots_before, 'lots_after': _lots(connection, params)}
            result = json.loads(json.dumps(result, default=str))
            token = hashlib.sha256(json.dumps({'inputs': inputs, 'result': result, 'evidence': evidence['source_signature'],
                                               'note': note.strip(), 'cost_basis': cost_basis}, default=str, sort_keys=True).encode()).hexdigest()
            if preview_token is not None and token != preview_token:
                raise SplitCorrectionConflict('The accounting inputs or treatment changed. Preview the resolution again.')
            result.update(preview_token=token, applied=preview_token is not None)
            if preview_token is None:
                transaction.rollback()
            return result


def connected_instruments(connection: Connection, account_id: str, instrument_ids: set[str]) -> set[str]:
    result = set(instrument_ids)
    links = connection.execute(text(
        'SELECT r.source_instrument_id,r.destination_instrument_id FROM corporate_action_resolution r '
        'JOIN event_corp_action e USING(event_corp_action_id) WHERE e.account_id=:account_id'
    ), {'account_id': account_id}).all()
    while True:
        prior = set(result)
        for source, destination in links:
            pair = {str(value) for value in (source, destination) if value is not None}
            if result & pair:
                result.update(pair)
        if prior == result:
            return result


def rebuild_snapshots(connection: Connection, engine: Engine, account_id: str, instrument_ids: set[str],
                      snapshots: list[dict[str, Any]] | None = None) -> None:
    params = {'account_id': account_id, 'ids': sorted(instrument_ids)}
    snapshots = _snapshots(connection, params) if snapshots is None else snapshots
    scopes: dict[tuple[Any, ...], set[str]] = {}
    for row in snapshots:
        key = (row['report_date_local'], str(row['ingestion_run_id']) if row['ingestion_run_id'] else None, row['currency'])
        scopes.setdefault(key, set()).add(str(row['instrument_id']))
    for day in {row['report_date_local'] for row in snapshots}:
        rows = [row for row in snapshots if row['report_date_local'] == day]
        latest = max(rows, key=lambda row: row['valuation_created_at_utc'])
        key = (day, str(latest['ingestion_run_id']) if latest['ingestion_run_id'] else None, latest['currency'])
        scopes[key].update(instrument_ids - {str(row['instrument_id']) for row in rows})
    contexts = sorted(scopes, key=lambda value: (value[0], value[1] or '', value[2]))
    conids = connection.execute(text('SELECT conid FROM instrument WHERE account_id=:account_id AND instrument_id=ANY(CAST(:ids AS uuid[]))'), params).scalars().all()
    ledger = StockLedgerSnapshotService(SQLAlchemyLedgerSnapshotService(engine, connection=connection))
    for index, (day, run_id, currency) in enumerate(contexts):
        ledger.ledger_snapshot_build_and_persist(account_id=account_id, ingestion_run_id=run_id,
                                                report_date_local=str(day), functional_currency=currency,
                                                affected_conids=frozenset(conids), affected_currencies=frozenset(),
                                                snapshot_instrument_ids=frozenset(scopes[(day, run_id, currency)]),
                                                reconcile_position_lots=index == len(contexts) - 1)


def _snapshots(connection: Connection, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(text(
        'SELECT p.*,i.symbol,COALESCE(r.started_at_utc,p.calculated_at_utc,p.created_at_utc) AS valuation_created_at_utc '
        'FROM pnl_snapshot_daily p JOIN instrument i USING(instrument_id) LEFT JOIN ingestion_run r USING(ingestion_run_id) '
        'WHERE p.account_id=:account_id AND p.instrument_id=ANY(CAST(:ids AS uuid[])) ORDER BY p.report_date_local,p.instrument_id,p.currency'
    ), params).mappings()]


def _lots(connection: Connection, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(text(
        'SELECT i.symbol,l.open_event_trade_fill_id,l.open_event_corp_action_id,l.opened_at_utc,l.remaining_quantity,l.cost_basis_remaining, '
        '(SELECT p.currency FROM pnl_snapshot_daily p WHERE p.account_id=l.account_id AND p.instrument_id=l.instrument_id '
        'ORDER BY p.report_date_local DESC LIMIT 1) AS currency '
        'FROM position_lot l JOIN instrument i USING(instrument_id) WHERE l.account_id=:account_id '
        'AND l.instrument_id=ANY(CAST(:ids AS uuid[])) ORDER BY l.instrument_id,l.opened_at_utc,l.position_lot_id'
    ), params).mappings()]


def _accounting_inputs(connection: Connection, account_id: str) -> dict[str, Any]:
    """Bind the exact account inputs, including the unused side of a paired action."""
    result = {}
    for table in ('event_trade_fill', 'event_cashflow', 'event_corp_action', 'event_fx', 'instrument', 'pnl_snapshot_daily', 'position_lot'):
        result[table] = connection.execute(text(f'SELECT to_jsonb(t)::text FROM {table} t WHERE account_id=:account_id ORDER BY 1'),
                                           {'account_id': account_id}).scalars().all()
    for table in ('corporate_action_manual_case', 'corporate_action_resolution'):
        result[table] = connection.execute(text(f'SELECT to_jsonb(t)::text FROM {table} t JOIN event_corp_action e USING(event_corp_action_id) '
                                                'WHERE e.account_id=:account_id ORDER BY 1'), {'account_id': account_id}).scalars().all()
    # Failed imports retain raw rows but cannot change the committed accounting preview.
    result['sources'] = connection.execute(text(
        "SELECT r.raw_record_id,r.source_payload::text FROM raw_record r WHERE r.account_id=:account_id AND ("
        "(r.section_name='OpenPositions' AND r.ingestion_run_id IN "
        "(SELECT ingestion_run_id FROM pnl_snapshot_daily WHERE account_id=:account_id)) OR r.raw_record_id IN "
        "(SELECT source_raw_record_id FROM event_trade_fill WHERE account_id=:account_id UNION "
        "SELECT source_raw_record_id FROM event_fx WHERE account_id=:account_id) OR "
        "(r.section_name='CorporateActions' AND r.raw_artifact_id IN "
        "(SELECT source.raw_artifact_id FROM event_corp_action e JOIN raw_record source "
        "ON source.raw_record_id=e.source_raw_record_id WHERE e.account_id=:account_id))) ORDER BY r.raw_record_id"
    ), {'account_id': account_id}).all()
    return result


def refresh_security_resolutions(connection: Connection, engine: Engine, source_ids: list[str], account_ids: list[str]) -> set[str]:
    """Rebind identical replay and rebuild both sides when approved legs change."""
    rows = connection.execute(text(
        'SELECT r.*,e.account_id,e.report_date_local AS current_date,c.instrument_id AS case_instrument_id '
        'FROM corporate_action_resolution r JOIN event_corp_action e USING(event_corp_action_id) '
        'JOIN corporate_action_manual_case c USING(event_corp_action_id) '
        'WHERE e.source_raw_record_id=ANY(CAST(:ids AS uuid[])) OR e.account_id=ANY(CAST(:accounts AS text[])) FOR UPDATE OF r'
    ), {'ids': source_ids, 'accounts': account_ids}).mappings().all()
    scopes: dict[str, set[str]] = {}
    for row in rows:
        evidence = action_evidence(connection, row['event_corp_action_id'])
        valid = row['source_signature'] == evidence['source_signature']
        event_id = row['event_corp_action_id']
        connection.execute(text('UPDATE corporate_action_resolution SET active=:active,invalidated_reason=NULL WHERE event_corp_action_id=:id'),
                           {'active': valid, 'id': event_id})
        connection.execute(text('UPDATE event_corp_action SET requires_manual=:manual,provisional=:manual WHERE event_corp_action_id=:id'),
                           {'manual': not valid, 'id': event_id})
        connection.execute(text(
            "UPDATE corporate_action_manual_case SET status=:status,resolved_at_utc=CASE WHEN :active THEN COALESCE(resolved_at_utc,now()) ELSE NULL END, "
            "resolution_note=:note,updated_at_utc=CASE WHEN status<>:status THEN now() ELSE updated_at_utc END "
            "WHERE event_corp_action_id=:id"
        ), {'status': 'resolved' if valid else 'open', 'active': valid, 'note': row['note'], 'id': event_id})
        ids = {str(value) for value in (row['source_instrument_id'], row['destination_instrument_id'], row['case_instrument_id']) if value}
        ids.update(leg['instrument_id'] for leg in evidence['broker_legs'] if leg['instrument_id'])
        ids = connected_instruments(connection, row['account_id'], ids)
        # Rebuild unchanged approvals too: a corrected earlier trade changes transferred basis.
        scopes.setdefault(row['account_id'], set()).update(ids)
        connection.execute(text(
            'UPDATE pnl_snapshot_daily SET calculation_provisional=true,provisional=true WHERE account_id=:account_id '
            'AND instrument_id=ANY(CAST(:ids AS uuid[])) AND report_date_local>=:day'
        ), {'account_id': row['account_id'], 'ids': sorted(ids), 'day': min(row['report_date_local'], row['current_date'])})
    for account_id, ids in scopes.items():
        while True:
            try:
                rebuild_snapshots(connection, engine, account_id, ids)
                break
            except FifoSecurityMovementError as error:
                changed = connection.execute(text(
                    'UPDATE corporate_action_resolution SET active=false,invalidated_reason=:reason '
                    'WHERE event_corp_action_id=:id AND active RETURNING event_corp_action_id'
                ), {'id': error.event_corp_action_id, 'reason': str(error)}).scalar_one_or_none()
                if changed is None:
                    raise
                connection.execute(text('UPDATE event_corp_action SET requires_manual=true,provisional=true WHERE event_corp_action_id=:id'), {'id': changed})
                connection.execute(text("UPDATE corporate_action_manual_case SET status='open',resolved_at_utc=NULL,updated_at_utc=now() "
                                        'WHERE event_corp_action_id=:id'), {'id': changed})
    return set().union(*scopes.values()) if scopes else set()
