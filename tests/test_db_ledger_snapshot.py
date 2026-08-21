"""Focused repository tests for scoped ledger snapshot queries."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.db.interfaces import LedgerOpenPositionValuationRecord, PositionLotUpsertRequest
from app.db.ledger_snapshot import SQLAlchemyLedgerSnapshotService


class _ResultStub:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> _ResultStub:
        return self

    def all(self) -> list[dict]:
        return self._rows


class _ConnectionStub:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.executed_queries: list[str] = []
        self.executed_parameters: list[object] = []

    def __enter__(self) -> _ConnectionStub:
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        _ = (exc_type, exc, traceback)
        return False

    def execute(self, statement, parameters=None) -> _ResultStub:
        self.executed_queries.append(str(statement))
        self.executed_parameters.append(parameters)
        return _ResultStub(self.rows)


class _EngineStub:
    def __init__(self, connection: _ConnectionStub) -> None:
        self.connection = connection

    def connect(self) -> _ConnectionStub:
        return self.connection

    def begin(self) -> _ConnectionStub:
        return self.connection


def test_scope_lookup_uses_conid_currency_union_and_normalizes_ids() -> None:
    """Resolve either affected key and return UUID-backed identifiers as strings."""

    instrument_id = uuid4()
    connection = _ConnectionStub(rows=[{"instrument_id": instrument_id}])
    repository = SQLAlchemyLedgerSnapshotService(_EngineStub(connection))

    result = repository.db_ledger_instrument_ids_for_scope(
        account_id="U1",
        conids=("100",),
        currencies=("EUR",),
    )

    assert result == [str(instrument_id)]
    assert "conid = ANY(:conids) OR currency = ANY(:currencies)" in connection.executed_queries[0]
    assert "ORDER BY instrument_id" in connection.executed_queries[0]
    assert connection.executed_parameters[0] == {
        "account_id": "U1",
        "conids": ["100"],
        "currencies": ["EUR"],
    }


def test_instrument_currency_list_is_scoped_to_selected_ids() -> None:
    """Read distinct currencies only for the selected canonical instruments."""

    instrument_id = str(uuid4())
    connection = _ConnectionStub(rows=[{"currency": "GBP"}, {"currency": "USD"}])
    repository = SQLAlchemyLedgerSnapshotService(_EngineStub(connection))

    result = repository.db_ledger_instrument_currency_list((instrument_id,))

    assert result == ["GBP", "USD"]
    assert "instrument_id = ANY(CAST(:instrument_ids AS uuid[]))" in connection.executed_queries[0]
    assert connection.executed_parameters[0] == {"instrument_ids": [instrument_id]}


def test_scoped_ledger_reads_apply_instrument_and_currency_filters() -> None:
    """Constrain every scoped ledger source query at the database boundary."""

    instrument_id = str(uuid4())
    connection = _ConnectionStub()
    repository = SQLAlchemyLedgerSnapshotService(_EngineStub(connection))

    repository.db_ledger_trade_fill_list_for_account("U1", "2026-08-21", (instrument_id,))
    repository.db_ledger_cashflow_list_for_account("U1", "2026-08-21", (instrument_id,))
    repository.db_ledger_corporate_action_list_for_account("U1", "2026-08-21", (instrument_id,))
    repository.db_ledger_open_position_valuation_list_for_run("U1", str(uuid4()), (instrument_id,))
    repository.db_ledger_fx_rate_list_for_account("U1", "2026-08-21", ("EUR", "USD"))

    assert "etf.instrument_id = ANY(CAST(:instrument_ids AS uuid[]))" in connection.executed_queries[0]
    assert "instrument_id = ANY(CAST(:instrument_ids AS uuid[]))" in connection.executed_queries[1]
    assert "event.instrument_id = ANY(CAST(:instrument_ids AS uuid[]))" in connection.executed_queries[2]
    assert "i.instrument_id = ANY(CAST(:instrument_ids AS uuid[]))" in connection.executed_queries[3]
    assert "currency = ANY(:currencies)" in connection.executed_queries[4]
    assert connection.executed_parameters[0]["instrument_ids"] == [instrument_id]
    assert connection.executed_parameters[4]["currencies"] == ["EUR", "USD"]


def test_open_position_read_includes_option_cost_fx_and_multiplier() -> None:
    instrument_id = uuid4()
    connection = _ConnectionStub(rows=[{
        "instrument_id": instrument_id,
        "asset_category": "OPT",
        "currency": "USD",
        "position_qty": Decimal("-1"),
        "mark_price": Decimal("2.21"),
        "cost_basis_money": Decimal("-28"),
        "broker_unrealized_pnl": Decimal("-193"),
        "fx_rate_to_base": Decimal("1"),
        "multiplier": Decimal("100"),
        "report_date_local": date(2026, 8, 20),
    }])
    repository = SQLAlchemyLedgerSnapshotService(_EngineStub(connection))

    rows = repository.db_ledger_open_position_valuation_list_for_run(
        "U1", str(uuid4())
    )

    assert rows == [LedgerOpenPositionValuationRecord(
        instrument_id=instrument_id,
        asset_category="OPT",
        currency="USD",
        position_qty="-1",
        mark_price="2.21",
        cost_basis_money="-28",
        broker_unrealized_pnl="-193",
        fx_rate_to_base="1",
        multiplier="100",
        report_date_local=date(2026, 8, 20),
    )]
    query = connection.executed_queries[0]
    assert "assetCategory" in query
    assert "costBasisMoney" in query
    assert "fxRateToBase" in query
    assert "multiplier" in query
    assert "assetCategory', '') = 'STK'" not in query


def test_open_position_read_preserves_blank_optional_values_as_none() -> None:
    instrument_id = uuid4()
    connection = _ConnectionStub(rows=[{
        "instrument_id": instrument_id,
        "asset_category": "STK",
        "currency": "USD",
        "position_qty": Decimal("5"),
        "mark_price": None,
        "cost_basis_money": None,
        "broker_unrealized_pnl": None,
        "fx_rate_to_base": None,
        "multiplier": None,
        "report_date_local": date(2026, 8, 20),
    }])
    repository = SQLAlchemyLedgerSnapshotService(_EngineStub(connection))
    row = repository.db_ledger_open_position_valuation_list_for_run(
        "U1", str(uuid4())
    )[0]
    assert row.mark_price is None
    assert row.cost_basis_money is None
    assert row.broker_unrealized_pnl is None
    assert row.fx_rate_to_base is None
    assert row.multiplier is None


def test_scoped_lot_reconciliation_closes_and_replaces_only_selected_instruments() -> None:
    """Prevent an unrelated lot request from entering scoped persistence."""

    selected_id = str(uuid4())
    unrelated_id = str(uuid4())
    connection = _ConnectionStub()
    repository = SQLAlchemyLedgerSnapshotService(_EngineStub(connection))
    requests = [
        _position_lot_request(selected_id),
        _position_lot_request(unrelated_id),
    ]

    repository.db_position_lot_reconcile_open(
        account_id="U1",
        closed_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
        requests=requests,
        instrument_ids=(selected_id,),
    )

    assert "instrument_id = ANY(CAST(:instrument_ids AS uuid[]))" in connection.executed_queries[0]
    replacement_parameters = connection.executed_parameters[1]
    assert isinstance(replacement_parameters, list)
    assert [parameters["instrument_id"] for parameters in replacement_parameters] == [selected_id]


def _position_lot_request(instrument_id: str) -> PositionLotUpsertRequest:
    return PositionLotUpsertRequest(
        position_lot_id=str(uuid4()),
        account_id="U1",
        instrument_id=instrument_id,
        open_event_trade_fill_id=str(uuid4()),
        opened_at_utc=datetime(2026, 8, 21, tzinfo=timezone.utc),
        closed_at_utc=None,
        open_quantity="1",
        remaining_quantity="1",
        open_price="100",
        cost_basis_open="100",
        realized_pnl_to_date="0",
        status="open",
    )
