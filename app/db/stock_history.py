"""Read a stock and its options from canonical activity and existing ledger projections."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import re
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError


def db_stock_history(engine: Engine, account_id: str, instrument_id: UUID) -> dict[str, Any] | None:
    """Return all imported family activity with the latest cumulative P&L (never summed across dates)."""
    try:
        with engine.connect() as connection:
            instruments = [dict(row) for row in connection.execute(text(
                "WITH metadata AS (SELECT DISTINCT ON (r.source_payload->>'conid') "
                "r.source_payload->>'conid' AS conid, r.source_payload->>'underlyingConid' AS underlying_conid, "
                "r.source_payload->>'underlyingSymbol' AS underlying_symbol FROM raw_record r "
                "JOIN ingestion_run run USING (ingestion_run_id) WHERE r.account_id=:account_id "
                "AND run.status='success' AND r.section_name IN ('Trades', 'SecuritiesInfo', 'OpenPositions') "
                "AND (NULLIF(BTRIM(r.source_payload->>'underlyingConid'), '') IS NOT NULL "
                "OR NULLIF(BTRIM(r.source_payload->>'underlyingSymbol'), '') IS NOT NULL) "
                "ORDER BY r.source_payload->>'conid', r.report_date_local DESC NULLS LAST, "
                "r.created_at_utc DESC, r.raw_record_id DESC) "
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
                "SELECT DISTINCT ON (instrument_id) instrument_id, report_date_local, currency, position_qty, "
                "cost_basis, realized_pnl, unrealized_pnl, total_pnl, provisional FROM pnl_snapshot_daily "
                "WHERE account_id=:account_id AND instrument_id=ANY(:instrument_ids) "
                "ORDER BY instrument_id, report_date_local DESC"
            ), params).mappings()}
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
                 "event.net_cash AS amount, event.currency, raw.source_payload->>'description' AS description"),
                ('event_cashflow', 'event_cashflow_id', 'cashflow',
                 "event.effective_at_utc AS timestamp_utc, event.cash_action AS action, NULL AS quantity, "
                 "NULL AS price, event.amount, event.currency, raw.source_payload->>'description' AS description"),
                ('event_corp_action', 'event_corp_action_id', 'corporate_action',
                 "NULL AS timestamp_utc, event.reorg_code AS action, NULL AS quantity, NULL AS price, "
                 "NULL AS amount, NULL AS currency, event.description"),
            ):
                activity.extend(dict(row) for row in connection.execute(text(
                    f"SELECT event.{identifier} AS event_id, '{kind}' AS event_type, i.instrument_id, i.symbol, "
                    f"event.report_date_local, event.source_raw_record_id, {columns} FROM {table} event "
                    "JOIN raw_record raw ON raw.raw_record_id=event.source_raw_record_id "
                    "JOIN instrument i ON i.account_id=event.account_id AND "
                    + ("(i.instrument_id=event.instrument_id OR (event.instrument_id IS NULL AND i.conid=event.conid)) "
                       if kind == 'corporate_action' else "i.instrument_id=event.instrument_id ")
                    + "WHERE event.account_id=:account_id AND i.instrument_id=ANY(:instrument_ids)"
                ), params).mappings())
    except SQLAlchemyError as error:
        raise RuntimeError("stock history report failed") from error

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
    lot_quantities: dict[UUID, Decimal] = defaultdict(Decimal)
    lot_basis: dict[UUID, Decimal] = defaultdict(Decimal)
    open_lot_counts: dict[UUID, int] = defaultdict(int)
    for lot in lots:
        direction = Decimal('1') if lot['side'] == 'BUY' else Decimal('-1')
        lot_quantities[lot['instrument_id']] += direction * lot['remaining_quantity']
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
        'provisional': missing_snapshot or any(row['provisional'] for row in positions) or len(dates) > 1,
        'totals': totals, 'positions': positions, 'lots': lots, 'activity': activity,
    }


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
    family = []
    for row in instruments:
        candidate_conid, candidate_symbol = _underlying(row)
        related = (candidate_conid == root_conid if candidate_conid and root_conid
                   else bool(candidate_symbol and candidate_symbol == root_symbol))
        if row['instrument_id'] == root['instrument_id'] or (
            row['asset_category'].strip().upper() == 'OPT' and related
        ):
            family.append(row)
    return root, family
