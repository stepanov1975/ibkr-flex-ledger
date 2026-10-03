"""Accounting consequences of corporate-action revisions on a caller-owned connection."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from app.db.corporate_action_evidence import action_evidence, accounting_snapshot_rows, split_snapshot_rows
from app.db.interfaces import LedgerSecurityMovementRecord
from app.db.ledger_snapshot import SQLAlchemyLedgerSnapshotService
from app.ledger import StockLedgerSnapshotService
from app.ledger.fifo_engine import FifoSecurityMovementError


class SplitCorrectionConflict(ValueError):
    """The case or accounting inputs changed, or ingestion is still running."""


@dataclass(frozen=True)
class MovementRevisionState:
    """Accounting scope captured before a movement's evidence is changed.

    Attributes:
        instrument_ids: Connected source and destination holdings to rebuild.
        snapshots_before: Existing snapshot rows with their valuation contexts,
            retained in database order for the caller's preview comparison.
    """

    instrument_ids: frozenset[str]
    snapshots_before: list[dict[str, Any]]


class CorporateActionAccountingRevision:
    """Capture before-state and repair accounting after successful evidence writes.

    The caller owns the connection, locks, and transaction. Body failures skip
    repair; repair failures propagate so the caller can roll back all writes.

    Args:
        connection: Caller-owned connection used for evidence writes and repairs.
    """

    def __init__(self, connection: Connection) -> None:
        """Bind accounting work to the caller's connection without acquiring resources.

        Args:
            connection: Connection whose transaction and locks the caller owns.

        Returns:
            None: Stores the connection without opening a transaction or querying it.
        """
        self._connection = connection

    @contextmanager
    def movement_revision(self, account_id: str, event_id: UUID) -> Iterator[MovementRevisionState]:
        """Capture connected holdings, then rebuild their historical valuation contexts.

        The caller validates and locks the event before entry, writes the approved
        movement inside the context, and owns commit or rollback. A body exception
        skips repair and propagates unchanged. Repair failures also propagate;
        the caller must roll back all evidence and accounting writes on failure.

        Args:
            account_id: Account owning the validated corporate-action event.
            event_id: Locked event whose supported movement treatment will be written.

        Yields:
            MovementRevisionState: Connected instrument IDs and the existing
                snapshots used for both preview comparison and historical repair.

        Raises:
            SplitCorrectionConflict: Affected snapshots are missing or newer
                canonical activity has no snapshot.
            SQLAlchemyError: Capturing state or writing accounting fails.
            ValueError: Saved evidence or ledger inputs are inconsistent, including
                an ineligible FIFO security movement.
            RuntimeError: Required trade data or accounting persistence fails.
        """
        event = self._connection.execute(text(
            'SELECT report_date_local FROM event_corp_action WHERE account_id=:account_id AND event_corp_action_id=:id'
        ), {'account_id': account_id, 'id': event_id}).mappings().one()
        movement = action_evidence(self._connection, event_id)['movement']
        instrument_ids = {value for key, value in movement.items() if key.endswith('instrument_id') and value}
        instrument_ids = self._connected_instruments(account_id, instrument_ids)
        params = {'account_id': account_id, 'ids': sorted(instrument_ids)}
        before = accounting_snapshot_rows(self._connection, params)
        if not before or max(row['report_date_local'] for row in before) < event['report_date_local']:
            raise SplitCorrectionConflict('No affected snapshots are available. Import the broker statement first.')
        latest_date = max(row['report_date_local'] for row in before)
        for table in ('event_trade_fill', 'event_cashflow', 'event_corp_action'):
            if self._connection.scalar(text(
                f"SELECT EXISTS(SELECT 1 FROM {table} WHERE account_id=:account_id "
                "AND instrument_id=ANY(CAST(:ids AS uuid[])) AND report_date_local>:latest)"
            ), {**params, 'latest': latest_date}):
                raise SplitCorrectionConflict('Newer activity has no snapshot. Reprocess the failed ingestion first.')
        yield MovementRevisionState(frozenset(instrument_ids), before)
        self._rebuild_movement_snapshots(account_id, instrument_ids, before)

    @contextmanager
    def split_revision(self, account_id: str, event_id: UUID) -> Iterator[None]:
        """Recheck saved movements before rebuilding the split's affected dates.

        The caller validates the split, confirms snapshot readiness, and holds
        the account and case locks before entry. Write the approval inside the
        context. A body exception skips repair and propagates unchanged; repair
        failures also propagate. The caller must roll back all writes on failure.

        Args:
            account_id: Account owning the validated split event.
            event_id: Locked split event whose verified approval will be written.

        Yields:
            None: The caller writes evidence without receiving additional state.

        Raises:
            SQLAlchemyError: Capturing state or writing accounting fails.
            ValueError: Saved evidence or ledger inputs are inconsistent, including
                a FIFO movement that cannot be invalidated by the saved-approval retry.
            RuntimeError: Required trade data or accounting persistence fails.
        """
        event = self._connection.execute(text(
            'SELECT instrument_id, conid, report_date_local FROM event_corp_action '
            'WHERE account_id=:account_id AND event_corp_action_id=:id'
        ), {'account_id': account_id, 'id': event_id}).mappings().one()
        params = {'account_id': account_id, 'instrument_id': event['instrument_id'], 'start_date': event['report_date_local']}
        before = split_snapshot_rows(self._connection, params)
        yield
        refreshed_instrument_ids = self._refresh_security_resolutions([], [account_id])
        ledger = StockLedgerSnapshotService(SQLAlchemyLedgerSnapshotService(self._connection.engine, connection=self._connection))
        for snapshot in before:
            if str(event['instrument_id']) in refreshed_instrument_ids or snapshot['report_date_local'] < event['report_date_local']:
                continue
            ledger.ledger_snapshot_build_and_persist(
                account_id=account_id,
                ingestion_run_id=None if snapshot['ingestion_run_id'] is None else str(snapshot['ingestion_run_id']),
                report_date_local=str(snapshot['report_date_local']),
                functional_currency=snapshot['currency'],
                affected_conids=frozenset({event['conid']}),
                affected_currencies=frozenset(),
                reconcile_position_lots=snapshot['report_date_local'] == before[-1]['report_date_local'],
            )
        # Clear the manual-case cause on earlier dates without changing pre-action amounts.
        self._connection.execute(text(
            "UPDATE pnl_snapshot_daily SET provisional=calculation_provisional OR EXISTS "
            "(SELECT 1 FROM corporate_action_manual_case c WHERE c.instrument_id=:instrument_id AND c.status='open') "
            "OR EXISTS (SELECT 1 FROM event_corp_action e WHERE e.instrument_id=:instrument_id AND e.requires_manual) "
            "WHERE account_id=:account_id AND instrument_id=:instrument_id AND report_date_local<:start_date"
        ), params)

    @contextmanager
    def canonical_revision(self, action_requests: list[dict[str, Any]], account_ids: list[str]) -> Iterator[None]:
        """Invalidate old/new history before source replacement, then repair in order.

        Retained split approvals adjust the normalized requests in place. Exit
        rechecks saved movements, syncs manual cases, rebuilds dirty history, and
        refreshes provisional flags, all inside the caller's transaction.
        Entry may already write historical provisional flags. A body exception
        skips repair and propagates unchanged; repair failures also propagate.
        The caller must roll back entry, body, and repair writes on failure.

        Args:
            action_requests: Validated, normalized corporate-action UPSERT rows.
                Write these same dictionaries in the context body so retained
                approval flags are preserved. May be empty for other event or
                instrument metadata revisions.
            account_ids: Accounts whose changed events or instrument metadata
                require saved movement approvals to be rechecked.

        Yields:
            None: The caller writes canonical rows using the prepared requests.

        Raises:
            SQLAlchemyError: Preparing history or writing accounting fails.
            ValueError: Saved evidence or ledger inputs are inconsistent, including
                a FIFO movement that cannot be invalidated by the saved-approval retry.
            RuntimeError: Required trade data or accounting persistence fails.
        """
        affected_instrument_ids = {row['instrument_id'] for row in action_requests if row['instrument_id'] is not None}
        self._prepare_canonical_actions(action_requests, affected_instrument_ids)
        yield
        affected_instrument_ids.update(self._refresh_security_resolutions(
            [row['source_raw_record_id'] for row in action_requests], account_ids,
        ))
        if action_requests:
            self._repair_canonical_history(action_requests, affected_instrument_ids)

    def security_movements(self, account_id: str, through_report_date_local: str) -> list[LedgerSecurityMovementRecord]:
        """Read source-bound approved movements for the ledger's historical scope.

        Args:
            account_id: Account whose active movement approvals are needed.
            through_report_date_local: Inclusive business date in YYYY-MM-DD format.

        Returns:
            list[LedgerSecurityMovementRecord]: Active movements ordered by date
                and event identity; empty when no approved movement is in scope.

        Raises:
            ValueError: Current broker evidence differs from a saved approval.
            SQLAlchemyError: Reading movements or their source evidence fails.
        """
        rows = self._connection.execute(text(
            "SELECT r.*, e.source_raw_record_id FROM corporate_action_resolution r "
            "JOIN event_corp_action e USING(event_corp_action_id) "
            "WHERE e.account_id=:account_id AND r.active AND NOT e.requires_manual "
            "AND r.report_date_local<=CAST(:day AS date) ORDER BY r.report_date_local, r.event_corp_action_id"
        ), {'account_id': account_id, 'day': through_report_date_local}).mappings().all()
        result = []
        for row in rows:
            if action_evidence(self._connection, row['event_corp_action_id'])['source_signature'] != row['source_signature']:
                raise ValueError('The approved corporate-action evidence changed. Reprocess the broker statement before rebuilding accounting.')
            result.append(LedgerSecurityMovementRecord(
                event_corp_action_id=row['event_corp_action_id'], source_raw_record_id=row['source_raw_record_id'],
                source_instrument_id=row['source_instrument_id'], destination_instrument_id=row['destination_instrument_id'],
                report_date_local=row['report_date_local'], quantity=str(row['quantity']),
                cost_basis=None if row['cost_basis'] is None else str(row['cost_basis']), currency=row['currency'],
            ))
        return result

    def _connected_instruments(self, account_id: str, instrument_ids: set[str]) -> set[str]:
        result = set(instrument_ids)
        links = self._connection.execute(text(
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

    def _rebuild_movement_snapshots(self, account_id: str, instrument_ids: set[str],
                                    snapshots: list[dict[str, Any]] | None = None) -> None:
        params = {'account_id': account_id, 'ids': sorted(instrument_ids)}
        snapshots = accounting_snapshot_rows(self._connection, params) if snapshots is None else snapshots
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
        conids = self._connection.execute(text('SELECT conid FROM instrument WHERE account_id=:account_id AND instrument_id=ANY(CAST(:ids AS uuid[]))'), params).scalars().all()
        ledger = StockLedgerSnapshotService(SQLAlchemyLedgerSnapshotService(self._connection.engine, connection=self._connection))
        for index, (day, run_id, currency) in enumerate(contexts):
            ledger.ledger_snapshot_build_and_persist(account_id=account_id, ingestion_run_id=run_id,
                                                    report_date_local=str(day), functional_currency=currency,
                                                    affected_conids=frozenset(conids), affected_currencies=frozenset(),
                                                    snapshot_instrument_ids=frozenset(scopes[(day, run_id, currency)]),
                                                    reconcile_position_lots=index == len(contexts) - 1)

    def _refresh_security_resolutions(self, source_ids: list[str], account_ids: list[str]) -> set[str]:
        """Rebind identical replay and rebuild both sides when approved legs change."""
        rows = self._connection.execute(text(
            'SELECT r.*,e.account_id,e.report_date_local AS current_date,c.instrument_id AS case_instrument_id '
            'FROM corporate_action_resolution r JOIN event_corp_action e USING(event_corp_action_id) '
            'JOIN corporate_action_manual_case c USING(event_corp_action_id) '
            'WHERE e.source_raw_record_id=ANY(CAST(:ids AS uuid[])) OR e.account_id=ANY(CAST(:accounts AS text[])) FOR UPDATE OF r'
        ), {'ids': source_ids, 'accounts': account_ids}).mappings().all()
        scopes: dict[str, set[str]] = {}
        for row in rows:
            evidence = action_evidence(self._connection, row['event_corp_action_id'])
            valid = row['source_signature'] == evidence['source_signature']
            event_id = row['event_corp_action_id']
            self._connection.execute(text('UPDATE corporate_action_resolution SET active=:active,invalidated_reason=NULL WHERE event_corp_action_id=:id'),
                               {'active': valid, 'id': event_id})
            self._connection.execute(text('UPDATE event_corp_action SET requires_manual=:manual,provisional=:manual WHERE event_corp_action_id=:id'),
                               {'manual': not valid, 'id': event_id})
            self._connection.execute(text(
                "UPDATE corporate_action_manual_case SET status=:status,resolved_at_utc=CASE WHEN :active THEN COALESCE(resolved_at_utc,now()) ELSE NULL END, "
                "resolution_note=:note,updated_at_utc=CASE WHEN status<>:status THEN now() ELSE updated_at_utc END "
                "WHERE event_corp_action_id=:id"
            ), {'status': 'resolved' if valid else 'open', 'active': valid, 'note': row['note'], 'id': event_id})
            ids = {str(value) for value in (row['source_instrument_id'], row['destination_instrument_id'], row['case_instrument_id']) if value}
            ids.update(leg['instrument_id'] for leg in evidence['broker_legs'] if leg['instrument_id'])
            ids = self._connected_instruments(row['account_id'], ids)
            # Rebuild unchanged approvals too: a corrected earlier trade changes transferred basis.
            scopes.setdefault(row['account_id'], set()).update(ids)
            self._connection.execute(text(
                'UPDATE pnl_snapshot_daily SET calculation_provisional=true,provisional=true WHERE account_id=:account_id '
                'AND instrument_id=ANY(CAST(:ids AS uuid[])) AND report_date_local>=:day'
            ), {'account_id': row['account_id'], 'ids': sorted(ids), 'day': min(row['report_date_local'], row['current_date'])})
        for account_id, ids in scopes.items():
            while True:
                try:
                    self._rebuild_movement_snapshots(account_id, ids)
                    break
                except FifoSecurityMovementError as error:
                    changed = self._connection.execute(text(
                        'UPDATE corporate_action_resolution SET active=false,invalidated_reason=:reason '
                        'WHERE event_corp_action_id=:id AND active RETURNING event_corp_action_id'
                    ), {'id': error.event_corp_action_id, 'reason': str(error)}).scalar_one_or_none()
                    if changed is None:
                        raise
                    self._connection.execute(text('UPDATE event_corp_action SET requires_manual=true,provisional=true WHERE event_corp_action_id=:id'), {'id': changed})
                    self._connection.execute(text("UPDATE corporate_action_manual_case SET status='open',resolved_at_utc=NULL,updated_at_utc=now() "
                                            'WHERE event_corp_action_id=:id'), {'id': changed})
        return set().union(*scopes.values()) if scopes else set()

    def _prepare_canonical_actions(self, action_requests: list[dict[str, Any]], affected_instrument_ids: set[str]) -> None:
        for request in action_requests:
            if request["action_id"] is None:
                continue
            self._connection.execute(text(
                "SELECT event_corp_action_id FROM event_corp_action "
                "WHERE account_id=:account_id AND action_id=:action_id FOR UPDATE"
            ), request)
            # Resolve a saved approval against the incoming version before
            # UPSERT, avoiding a transient manual-state mutation on replay.
            if request["requires_manual"] and self._connection.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM event_corp_action e "
                "JOIN corporate_action_manual_case c ON c.event_corp_action_id=e.event_corp_action_id "
                "JOIN raw_record original ON original.raw_record_id=c.resolution_source_raw_record_id "
                "JOIN raw_record incoming ON incoming.raw_record_id=CAST(:source_raw_record_id AS uuid) "
                "WHERE e.account_id=:account_id AND e.action_id=:action_id AND c.split_factor IS NOT NULL "
                "AND original.source_payload=incoming.source_payload "
                "AND c.resolution_report_date_local=CAST(:report_date_local AS date) "
                "AND c.instrument_id=COALESCE(CAST(:instrument_id AS uuid), e.instrument_id) "
                "AND c.action_type=:reorg_code AND original.source_payload->>'conid'=:conid "
                "AND :reorg_code IN ('FORWARDSPLIT','REVERSESPLIT','STOCKDIV'))"
            ), request):
                request["requires_manual"] = False
                request["provisional"] = False
            invalidated = self._connection.execute(text(
                "UPDATE pnl_snapshot_daily p SET calculation_provisional=true, provisional=true "
                "FROM event_corp_action e, raw_record previous, raw_record incoming "
                "WHERE e.account_id=:account_id AND e.action_id=:action_id "
                "AND previous.raw_record_id=e.source_raw_record_id "
                "AND incoming.raw_record_id=CAST(:source_raw_record_id AS uuid) "
                "AND (previous.source_payload<>incoming.source_payload "
                "OR (e.requires_manual AND NOT :requires_manual) "
                "OR e.report_date_local<>CAST(:report_date_local AS date) "
                "OR e.instrument_id IS DISTINCT FROM COALESCE(CAST(:instrument_id AS uuid), e.instrument_id)) "
                "AND p.account_id=e.account_id "
                "AND (p.instrument_id=e.instrument_id OR p.instrument_id=CAST(:instrument_id AS uuid)) "
                "AND p.report_date_local>=LEAST(e.report_date_local, CAST(:report_date_local AS date)) "
                "RETURNING p.instrument_id"
            ), request).mappings().all()
            affected_instrument_ids.update(str(row["instrument_id"]) for row in invalidated)

    def _repair_canonical_history(self, action_requests: list[dict[str, Any]], affected_instrument_ids: set[str]) -> None:
        correction_scope = {"source_ids": [row["source_raw_record_id"] for row in action_requests]}
        self._connection.execute(text(
            "UPDATE corporate_action_manual_case c SET status='open', resolved_at_utc=NULL, updated_at_utc=now() "
            "FROM event_corp_action e, raw_record original, raw_record current "
            "WHERE c.event_corp_action_id=e.event_corp_action_id AND e.requires_manual "
            "AND c.status<>'open' AND original.raw_record_id=c.resolution_source_raw_record_id "
            "AND current.raw_record_id=e.source_raw_record_id AND (original.source_payload<>current.source_payload "
            "OR c.resolution_report_date_local IS DISTINCT FROM e.report_date_local "
            "OR c.instrument_id IS DISTINCT FROM e.instrument_id OR c.action_type<>e.reorg_code "
            "OR original.source_payload->>'conid' IS DISTINCT FROM e.conid) "
            "AND e.source_raw_record_id=ANY(CAST(:source_ids AS uuid[]))"
        ), correction_scope)
        self._connection.execute(text(
            "UPDATE corporate_action_manual_case c SET status='resolved', resolved_at_utc=now(), "
            "updated_at_utc=now(), resolution_note=CASE WHEN c.split_factor IS NULL "
            "THEN 'Automatically handled from explicit broker data.' ELSE c.resolution_note END, "
            "resolution_source_raw_record_id=CASE WHEN c.split_factor IS NULL THEN e.source_raw_record_id "
            "ELSE c.resolution_source_raw_record_id END "
            "FROM event_corp_action e WHERE c.event_corp_action_id=e.event_corp_action_id "
            "AND NOT e.requires_manual AND c.status='open' "
            "AND e.source_raw_record_id=ANY(CAST(:source_ids AS uuid[]))"
        ), correction_scope)
        self._connection.execute(
            text(
                "INSERT INTO corporate_action_manual_case ("
                "event_corp_action_id, action_type, instrument_id) "
                "SELECT event_corp_action_id, reorg_code, instrument_id "
                "FROM event_corp_action "
                "WHERE requires_manual = true AND instrument_id IS NOT NULL "
                "ON CONFLICT ON CONSTRAINT uq_corporate_action_manual_case_event DO NOTHING"
            )
        )
        self._connection.execute(
            text(
                "UPDATE event_corp_action AS event SET manual_case_id = manual_case.case_id, provisional = true "
                "FROM corporate_action_manual_case AS manual_case "
                "WHERE manual_case.event_corp_action_id = event.event_corp_action_id "
                "AND event.manual_case_id IS DISTINCT FROM manual_case.case_id"
            )
        )
        # Rebuild dirty history for both previous and current
        # instruments once their actions are computable. The horizon
        # guard permits only the latest eligible lot projection.
        history_scope = {"instrument_ids": sorted(affected_instrument_ids)}
        snapshots = self._connection.execute(text(
            "SELECT p.account_id, p.report_date_local, p.ingestion_run_id, p.currency, i.conid "
            "FROM pnl_snapshot_daily p JOIN instrument i USING(instrument_id) "
            "WHERE p.instrument_id=ANY(CAST(:instrument_ids AS uuid[])) AND p.calculation_provisional "
            "AND NOT EXISTS (SELECT 1 FROM event_corp_action pending "
            "WHERE pending.instrument_id=p.instrument_id AND pending.requires_manual "
            "AND pending.report_date_local<=p.report_date_local) "
            "ORDER BY p.report_date_local, p.instrument_id"
        ), history_scope).mappings().all()
        ledger = StockLedgerSnapshotService(SQLAlchemyLedgerSnapshotService(self._connection.engine, connection=self._connection))
        for snapshot in snapshots:
            ledger.ledger_snapshot_build_and_persist(
                account_id=snapshot["account_id"],
                ingestion_run_id=None if snapshot["ingestion_run_id"] is None else str(snapshot["ingestion_run_id"]),
                report_date_local=str(snapshot["report_date_local"]),
                functional_currency=snapshot["currency"],
                affected_conids=frozenset({snapshot["conid"]}),
                affected_currencies=frozenset(),
            )
        self._connection.execute(text(
            "UPDATE pnl_snapshot_daily p SET provisional=p.calculation_provisional OR EXISTS "
            "(SELECT 1 FROM corporate_action_manual_case pending WHERE pending.instrument_id=p.instrument_id AND pending.status='open') "
            "OR EXISTS (SELECT 1 FROM event_corp_action pending WHERE pending.instrument_id=p.instrument_id AND pending.requires_manual) "
            "WHERE p.instrument_id=ANY(CAST(:instrument_ids AS uuid[]))"
        ), history_scope)
