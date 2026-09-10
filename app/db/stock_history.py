"""Read a stock and its options from canonical activity and existing ledger projections."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.domain.fx_rates import select_conversion_rate
from .interfaces import LedgerFxRateRecord
from .ledger_snapshot import SQLAlchemyLedgerSnapshotService


def db_stock_history(engine: Engine, account_id: str, instrument_id: UUID) -> dict[str, Any] | None:
    """Return all imported family activity with the latest cumulative P&L (never summed across dates)."""
    try:
        with engine.connect() as connection:
            instruments = [dict(row) for row in connection.execute(text(
                "WITH committed_sources AS (SELECT raw.raw_record_id, raw.raw_artifact_id, "
                "raw.source_payload->>'conid' AS conid FROM raw_record raw JOIN ("
                "SELECT source_raw_record_id FROM event_trade_fill WHERE account_id=:account_id UNION "
                "SELECT source_raw_record_id FROM event_cashflow WHERE account_id=:account_id UNION "
                "SELECT source_raw_record_id FROM event_corp_action WHERE account_id=:account_id"
                ") event ON event.source_raw_record_id=raw.raw_record_id), "
                "metadata_rows AS (SELECT r.source_payload->>'conid' AS conid, "
                "NULLIF(BTRIM(r.source_payload->>'underlyingConid'), '') AS underlying_conid, "
                "NULLIF(BTRIM(r.source_payload->>'underlyingSymbol'), '') AS underlying_symbol, "
                "r.report_date_local, r.created_at_utc, r.raw_record_id FROM raw_record r "
                "JOIN ingestion_run run ON run.ingestion_run_id=r.ingestion_run_id "
                "LEFT JOIN raw_artifact artifact ON artifact.raw_artifact_id=r.raw_artifact_id "
                "LEFT JOIN ingestion_run completion ON completion.ingestion_run_id=artifact.completed_ingestion_run_id "
                "WHERE r.account_id=:account_id AND (completion.status='success' "
                "OR (artifact.completed_ingestion_run_id IS NULL AND run.status='success') "
                "OR artifact.valuation_pending_at_utc IS NOT NULL "
                "OR EXISTS (SELECT 1 FROM committed_sources committed WHERE committed.raw_record_id=r.raw_record_id "
                "OR (committed.raw_artifact_id=r.raw_artifact_id AND committed.conid=r.source_payload->>'conid'))) "
                "AND r.section_name IN ('Trades', 'SecuritiesInfo', 'OpenPositions', 'CashTransactions', 'CorporateActions') "
                "), metadata AS (SELECT conid, "
                "(array_agg(underlying_conid ORDER BY report_date_local DESC NULLS LAST, created_at_utc DESC, "
                "raw_record_id DESC) FILTER (WHERE underlying_conid IS NOT NULL))[1] AS underlying_conid, "
                "(array_agg(underlying_symbol ORDER BY report_date_local DESC NULLS LAST, created_at_utc DESC, "
                "raw_record_id DESC) FILTER (WHERE underlying_symbol IS NOT NULL))[1] AS underlying_symbol "
                "FROM metadata_rows GROUP BY conid) "
                "SELECT i.instrument_id, i.conid, i.symbol, i.asset_category, i.description, "
                "m.underlying_conid, m.underlying_symbol FROM instrument i LEFT JOIN metadata m USING (conid) "
                "WHERE i.account_id=:account_id AND UPPER(BTRIM(i.asset_category)) NOT IN ('CASH', 'FX') "
                "ORDER BY i.symbol, i.instrument_id"
            ), {"account_id": account_id}).mappings()]
            selected = next((row for row in instruments if row['instrument_id'] == instrument_id), None)
            if selected is None:
                return None
            root, family = _stock_family(selected, instruments)
            params = {"account_id": account_id, "instrument_ids": [row['instrument_id'] for row in family]}
            snapshots = {row['instrument_id']: dict(row) for row in connection.execute(text(
                "SELECT DISTINCT ON (s.instrument_id) s.instrument_id, s.report_date_local, s.currency, s.position_qty, "
                "s.cost_basis, s.realized_pnl, s.unrealized_pnl, s.total_pnl, s.provisional, "
                "s.calculated_at_utc, s.ingestion_run_id, s.fx_dependencies FROM pnl_snapshot_daily s "
                "WHERE s.account_id=:account_id AND s.instrument_id=ANY(:instrument_ids) "
                "ORDER BY s.instrument_id, s.report_date_local DESC"
            ), params).mappings()}
            valuation_params = {
                **params, 'conids': [row['conid'] for row in family],
                'first_date': min((row['report_date_local'] for row in snapshots.values()), default=None),
                'snapshot_runs': [row['ingestion_run_id'] for row in snapshots.values() if row['ingestion_run_id']],
            }
            valuations = [dict(row) for row in connection.execute(text(
                "SELECT r.ingestion_run_id, r.raw_artifact_id, COALESCE(a.report_date_local,r.report_date_local) AS report_date_local, "
                "COALESCE(a.valuation_pending_at_utc,a.created_at_utc,min(r.created_at_utc)) AS recorded_at_utc, "
                "jsonb_object_agg(r.source_payload->>'conid', "
                "jsonb_build_object('payload',r.source_payload,'raw_id',r.raw_record_id) ORDER BY r.raw_record_id) "
                "FILTER (WHERE r.source_payload->>'conid'=ANY(:conids) "
                "AND r.source_row_ref LIKE 'OpenPositions:OpenPosition:%') AS positions "
                "FROM raw_record r LEFT JOIN raw_artifact a ON a.raw_artifact_id=r.raw_artifact_id "
                "JOIN ingestion_run run ON run.ingestion_run_id=r.ingestion_run_id "
                "LEFT JOIN ingestion_run completion ON completion.ingestion_run_id=a.completed_ingestion_run_id "
                "WHERE r.account_id=:account_id AND r.section_name='OpenPositions' "
                "AND (a.valuation_pending_at_utc IS NOT NULL OR completion.status='success' "
                "OR (a.completed_ingestion_run_id IS NULL AND run.status='success')) "
                "AND (CAST(:first_date AS date) IS NULL OR COALESCE(a.report_date_local,r.report_date_local)>=:first_date "
                "OR COALESCE(a.report_date_local,r.report_date_local) IS NULL "
                "OR r.ingestion_run_id=ANY(:snapshot_runs)) "
                "GROUP BY r.ingestion_run_id,r.raw_artifact_id,a.report_date_local,r.report_date_local, "
                "a.valuation_pending_at_utc,a.created_at_utc"
            ), valuation_params).mappings()]
            fx_rates = SQLAlchemyLedgerSnapshotService(engine, connection=connection).db_ledger_fx_rate_list_for_account(
                account_id, max(
                    [row['report_date_local'] for row in snapshots.values()]
                    + [row['report_date_local'] for row in valuations if row['report_date_local'] is not None]
                ).isoformat(),
            ) if snapshots else []
            lots = [dict(row) for row in connection.execute(text(
                "SELECT l.instrument_id, l.open_event_trade_fill_id, l.opened_at_utc, l.closed_at_utc, "
                "l.open_quantity, l.remaining_quantity, l.cost_basis_remaining, l.realized_pnl_to_date, "
                "l.status, t.side FROM position_lot l JOIN event_trade_fill t "
                "ON t.event_trade_fill_id=l.open_event_trade_fill_id AND t.account_id=l.account_id "
                "WHERE l.account_id=:account_id AND l.instrument_id=ANY(:instrument_ids) "
                "ORDER BY l.opened_at_utc DESC, l.position_lot_id"
            ), params).mappings()]
            activity: list[dict[str, Any]] = []
            for table, identifier, kind, columns in (
                ('event_trade_fill', 'event_trade_fill_id', 'trade',
                 "event.trade_timestamp_utc AS timestamp_utc, event.side AS action, event.quantity, event.price, "
                 "event.net_cash AS amount, event.currency, event.description"),
                ('event_cashflow', 'event_cashflow_id', 'cashflow',
                 "event.effective_at_utc AS timestamp_utc, event.cash_action AS action, NULL AS quantity, "
                 "NULL AS price, event.amount, event.currency, raw.source_payload->>'description' AS description"),
                ('event_corp_action', 'event_corp_action_id', 'corporate_action',
                 "NULL AS timestamp_utc, event.reorg_code AS action, NULL AS quantity, NULL AS price, "
                 "NULL AS amount, NULL AS currency, event.description"),
            ):
                activity.extend(dict(row) for row in connection.execute(text(
                    f"SELECT event.{identifier} AS event_id, '{kind}' AS event_type, i.instrument_id, i.symbol, "
                    "event.updated_at_utc AS recorded_at_utc, "
                    f"event.report_date_local, event.source_raw_record_id, {columns} FROM {table} event "
                    "JOIN raw_record raw ON raw.raw_record_id=event.source_raw_record_id "
                    "JOIN instrument i ON i.account_id=event.account_id AND "
                    + ("(i.instrument_id=event.instrument_id OR (event.instrument_id IS NULL AND i.conid=event.conid)) "
                       if kind == 'corporate_action' else "i.instrument_id=event.instrument_id ")
                    + "WHERE event.account_id=:account_id AND i.instrument_id=ANY(:instrument_ids)"
                ), params).mappings())
    except SQLAlchemyError as error:
        raise RuntimeError("stock history report failed") from error

    # Unknown legacy calculation times require a rebuild before freshness can be established.
    stale_instruments = {identifier for identifier, row in snapshots.items()
                         if row['calculated_at_utc'] is None or row['fx_dependencies'] is None}
    for event in activity:
        recorded_at = event.pop('recorded_at_utc')
        snapshot = snapshots.get(event['instrument_id'], {})
        if snapshot and (snapshot['calculated_at_utc'] is None
                         or event['report_date_local'] > snapshot['report_date_local']
                         or recorded_at > snapshot['calculated_at_utc']):
            stale_instruments.add(event['instrument_id'])
    origins: dict[tuple[UUID, str], dict[str, Any]] = {}
    lot_quantities: dict[UUID, Decimal] = defaultdict(Decimal)
    for lot in lots:
        lot_quantities[lot['instrument_id']] += lot['remaining_quantity'] * (1 if lot['side'] == 'BUY' else -1)
    for valuation in valuations:
        for conid, position in (valuation['positions'] or {}).items():
            key = valuation['ingestion_run_id'], conid
            if key not in origins or position['raw_id'] > origins[key]['raw_id']:
                origins[key] = position
    for member in family:
        identifier = member['instrument_id']
        snapshot = snapshots.get(identifier, {})
        if not snapshot or snapshot['calculated_at_utc'] is None:
            continue
        origin = _valuation_inputs(
            origins.get((snapshot['ingestion_run_id'], member['conid']), {}).get('payload'),
            lot_quantities[identifier], snapshot['currency'], snapshot['report_date_local'], fx_rates,
        )
        for valuation in valuations:
            valuation_date = valuation['report_date_local'] or snapshot['report_date_local']
            if valuation_date < snapshot['report_date_local']:
                continue
            candidate = _valuation_inputs(
                (valuation['positions'] or {}).get(member['conid'], {}).get('payload'),
                lot_quantities[identifier], snapshot['currency'], valuation_date, fx_rates,
            )
            if (candidate != origin and (candidate is not None or snapshot['position_qty'] != 0)
                    and (valuation_date > snapshot['report_date_local']
                         or valuation['recorded_at_utc'] > snapshot['calculated_at_utc'])):
                stale_instruments.add(identifier)
        for dependency in snapshot['fx_dependencies'] or []:
            selected_rate = select_conversion_rate(
                dependency['currency'], dependency['functional_currency'], date.fromisoformat(dependency['date']), fx_rates,
            )
            current_rate = Decimal(selected_rate.fx_rate) if selected_rate and selected_rate.fx_rate is not None else None
            previous_rate = Decimal(dependency['rate']) if dependency['rate'] is not None else None
            if current_rate != previous_rate:
                stale_instruments.add(identifier)
    for identifier in stale_instruments:
        snapshots[identifier]['provisional'] = True

    positions = []
    for member in family:
        snapshot = snapshots.get(member['instrument_id'], {})
        positions.append({
            **{key: member[key] for key in ('instrument_id', 'conid', 'symbol', 'asset_category')},
            **{key: snapshot.get(key) for key in (
                'report_date_local', 'currency', 'position_qty', 'realized_pnl', 'unrealized_pnl', 'total_pnl',
            )},
            'provisional': snapshot.get('provisional', True),
        })
    members = {row['instrument_id']: row for row in family}
    lot_basis: dict[UUID, Decimal] = defaultdict(Decimal)
    open_lot_counts: dict[UUID, int] = defaultdict(int)
    for lot in lots:
        lot_basis[lot['instrument_id']] += lot['cost_basis_remaining']
        open_lot_counts[lot['instrument_id']] += bool(lot['remaining_quantity'])
    for lot in lots:
        member = members[lot['instrument_id']]
        snapshot = snapshots.get(lot['instrument_id'], {})
        direction = Decimal('1') if lot['side'] == 'BUY' else Decimal('-1')
        remaining = direction * lot['remaining_quantity']
        unrealized = Decimal('0') if remaining == 0 else None
        # Lots and the aggregate snapshot are rounded independently to NUMERIC(24,8).
        basis_tolerance = Decimal('0.000000005') * (open_lot_counts[lot['instrument_id']] + 1)
        if (remaining and snapshot and not snapshot['provisional']
                and snapshot['position_qty'] == lot_quantities[lot['instrument_id']]
                and snapshot['cost_basis'] is not None
                and abs(snapshot['cost_basis'] - lot_basis[lot['instrument_id']]) <= basis_tolerance
                and snapshot['position_qty'] != 0):
            unit_value = (snapshot['cost_basis'] + snapshot['unrealized_pnl']) / snapshot['position_qty']
            unrealized = remaining * unit_value - lot['cost_basis_remaining']
        lot.update(
            conid=member['conid'], symbol=member['symbol'], asset_category=member['asset_category'],
            currency=snapshot['currency'] if snapshot else None,
            open_quantity=direction * lot['open_quantity'], remaining_quantity=remaining,
            realized_pnl=lot.pop('realized_pnl_to_date') if snapshot else None, unrealized_pnl=unrealized,
            provisional=snapshot['provisional'] if snapshot else True,
            status=('closed' if remaining == 0 else 'partially_closed'
                    if lot['remaining_quantity'] < lot['open_quantity'] else 'open'),
        )

    totals = []
    missing_snapshot = len(snapshots) != len(family)
    currencies = sorted({row['currency'] for row in positions if row['currency']})
    for currency in currencies or [None]:
        group = [row for row in positions if row['currency'] == currency]
        totals.append({
            'currency': currency,
            **{key: None if missing_snapshot else sum((row[key] for row in group), Decimal('0'))
               for key in ('realized_pnl', 'unrealized_pnl', 'total_pnl')},
        })
    dates = {row['report_date_local'] for row in snapshots.values()}
    activity.sort(key=lambda row: (
        row['report_date_local'], row['timestamp_utc'].isoformat() if row['timestamp_utc'] else '', str(row['event_id']),
    ), reverse=True)
    return {
        'instrument_id': root['instrument_id'], 'symbol': root['symbol'], 'description': root['description'],
        'report_date_local': max(dates) if dates else None,
        'stale': bool(stale_instruments),
        'provisional': missing_snapshot or any(row['provisional'] for row in positions),
        'totals': totals, 'positions': positions, 'lots': lots, 'activity': activity,
    }


def _valuation_inputs(
    payload: dict[str, Any] | None, fifo_quantity: Decimal, base_currency: str,
    report_date: date, fx_rates: list[LedgerFxRateRecord],
) -> dict[str, Any] | None:
    """Compare parsed valuation inputs, ignoring broker labels and numeric formatting."""
    if payload is None:
        return None
    result: dict[str, Any] = {'currency': (payload.get('currency') or '').strip().upper()}
    for key in ('position', 'markPrice', 'costBasisMoney', 'fifoPnlUnrealized', 'fxRateToBase', 'multiplier'):
        value = (payload.get(key) or '').strip()
        try:
            result[key] = None if value in ('', '-', '--', 'N/A') else Decimal(value.replace(',', ''))
            if isinstance(result[key], Decimal) and not result[key].is_finite():
                result[key] = value
        except InvalidOperation:
            # Failed imports can retain malformed raw values; keep them distinct.
            result[key] = value
    if result['position'] == fifo_quantity:
        # Reconciled quantities use FIFO basis and broker mark, not broker P&L.
        result.pop('costBasisMoney')
        result.pop('fifoPnlUnrealized')
    elif result['fifoPnlUnrealized'] is not None:
        # Unreconciled positions prefer the broker's P&L over its mark.
        result.pop('markPrice')
        result.pop('multiplier')
    if result['currency'] == base_currency.strip().upper():
        result.pop('fxRateToBase')
    elif not isinstance(result['fxRateToBase'], Decimal) or result['fxRateToBase'] <= 0:
        selected_rate = select_conversion_rate(result['currency'], base_currency, report_date, fx_rates)
        result['fxRateToBase'] = (
            Decimal(selected_rate.fx_rate) if selected_rate and selected_rate.fx_rate is not None else None
        )
    if result['position'] == 0:
        for key in ('markPrice', 'multiplier', 'fifoPnlUnrealized'):
            result.pop(key, None)
        if result.get('costBasisMoney') is None:
            result.pop('currency')
            result.pop('fxRateToBase', None)
    return result


def _underlying(instrument: dict[str, Any]) -> tuple[str | None, str | None]:
    if instrument['asset_category'].strip().upper() != 'OPT':
        return instrument['conid'], instrument['symbol']
    conid = (instrument.get('underlying_conid') or '').strip()
    symbol = (instrument.get('underlying_symbol') or '').strip()
    if not symbol:
        match = re.fullmatch(r'(.+?)\s*\d{6}[CP]\d{8}', instrument['symbol'])
        symbol = match[1].strip() if match else ''
    return conid or None, symbol or None


def _stock_family(selected: dict[str, Any], instruments: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    conid, symbol = _underlying(selected)
    stocks = [row for row in instruments if row['asset_category'].strip().upper() == 'STK']
    matches = [row for row in stocks if row['conid'] == conid] if conid else [
        row for row in stocks if row['symbol'] == symbol
    ]
    root = matches[0] if len(matches) == 1 else selected
    root_conid, root_symbol = _underlying(root)
    known_underlyings = [_underlying(row) for row in instruments if row['asset_category'].strip().upper() in ('STK', 'OPT')]
    symbol_is_unambiguous = len({conid for conid, symbol in known_underlyings if conid and symbol == root_symbol}) <= 1
    family = []
    for row in instruments:
        candidate_conid, candidate_symbol = _underlying(row)
        related = (candidate_conid == root_conid if candidate_conid and root_conid
                   else bool(symbol_is_unambiguous and candidate_symbol and candidate_symbol == root_symbol))
        if row['instrument_id'] == root['instrument_id'] or (
            row['asset_category'].strip().upper() == 'OPT' and related
        ):
            family.append(row)
    return root, family
