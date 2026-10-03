"""Source-bound security movement resolutions and atomic accounting previews."""

from decimal import Decimal
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from app.db.corporate_action_accounting import CorporateActionAccountingRevision, SplitCorrectionConflict
from app.db.corporate_action_evidence import action_evidence, accounting_inputs, accounting_lot_rows, accounting_snapshot_rows
from app.db.ingestion_run import SQLAlchemyIngestionRunService


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
            SQLAlchemyIngestionRunService(self._engine).db_correction_account_lock(connection, self._account_id)
            case = connection.execute(text(
                "SELECT c.*, e.account_id, e.action_id, e.requires_manual, e.source_raw_record_id, e.report_date_local "
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
            with CorporateActionAccountingRevision(connection).movement_revision(self._account_id, case['event_corp_action_id']) as revision:
                params = {'account_id': self._account_id, 'ids': sorted(revision.instrument_ids)}
                before = revision.snapshots_before
                inputs = accounting_inputs(connection, self._account_id)
                lots_before = accounting_lot_rows(connection, params)
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
            after = accounting_snapshot_rows(connection, params)
            old_by_key = {(row['instrument_id'], row['report_date_local'], row['currency']): row for row in before}
            fields = ('position_qty', 'cost_basis', 'realized_pnl', 'unrealized_pnl', 'total_pnl', 'provisional')
            snapshots = []
            for row in after:
                old = old_by_key.get((row['instrument_id'], row['report_date_local'], row['currency']), {})
                snapshots.append({'symbol': row['symbol'], 'report_date_local': str(row['report_date_local']),
                                  'currency': row['currency'], 'before': {field: old.get(field) for field in fields},
                                  'after': {field: row[field] for field in fields}})
            result = {'case_id': str(case_id), 'treatment': treatment,
                      'event': {'event_corp_action_id': str(case['event_corp_action_id']), 'action_id': case['action_id'],
                                'report_date_local': movement['report_date_local'],
                                'source_symbol': next((leg['symbol'] for leg in evidence['broker_legs']
                                                       if leg['instrument_id'] == movement['source_instrument_id']), None),
                                'destination_symbol': next(leg['symbol'] for leg in evidence['broker_legs']
                                                           if leg['instrument_id'] == movement['destination_instrument_id']),
                                'quantity': movement['quantity'], 'currency': movement['currency'],
                                'cost_basis': cost_basis, 'note': note.strip()},
                      'summary': ('Transfer the matched holding with its existing FIFO basis and acquisition dates.' if treatment == 'security_transfer'
                                  else f"Record {movement['quantity']} credited units with total cost basis {cost_basis} {movement['currency']}; parent basis stays unchanged."),
                      'snapshots': snapshots, 'lots_before': lots_before, 'lots_after': accounting_lot_rows(connection, params)}
            result = json.loads(json.dumps(result, default=str))
            token = hashlib.sha256(json.dumps({'inputs': inputs, 'result': result, 'evidence': evidence['source_signature'],
                                               'note': note.strip(), 'cost_basis': cost_basis}, default=str, sort_keys=True).encode()).hexdigest()
            if preview_token is not None and token != preview_token:
                raise SplitCorrectionConflict('The accounting inputs or treatment changed. Preview the resolution again.')
            result.update(preview_token=token, applied=preview_token is not None)
            if preview_token is None:
                transaction.rollback()
            return result
