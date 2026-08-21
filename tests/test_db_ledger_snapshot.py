"""Focused repository tests for scoped ledger snapshot queries."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import TracebackType
from typing import Literal, cast
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from app.db.interfaces import (
    LedgerOpenPositionValuationRecord,
    PositionLotUpsertRequest,
    SnapshotCleanupCandidate,
)
from app.db.ledger_snapshot import SQLAlchemyLedgerSnapshotService


class _ResultStub:
    def __init__(self, rows: list[dict[str, object]], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def mappings(self) -> _ResultStub:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _ConnectionStub:
    def __init__(
        self,
        rows: list[dict[str, object]] | None = None,
        rowcount: int = 0,
    ) -> None:
        self.rows = rows or []
        self.rowcount = rowcount
        self.executed_queries: list[str] = []
        self.executed_parameters: list[
            dict[str, object] | list[dict[str, object]] | None
        ] = []

    def __enter__(self) -> _ConnectionStub:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        _ = (exc_type, exc, traceback)
        return False

    def execute(
        self,
        statement: object,
        parameters: dict[str, object] | list[dict[str, object]] | None = None,
    ) -> _ResultStub:
        self.executed_queries.append(str(statement))
        self.executed_parameters.append(parameters)
        return _ResultStub(self.rows, rowcount=self.rowcount)


class _EngineStub:
    def __init__(self, connection: _ConnectionStub) -> None:
        self.connection = connection

    def connect(self) -> _ConnectionStub:
        return self.connection

    def begin(self) -> _ConnectionStub:
        return self.connection


def _repository(connection: _ConnectionStub) -> SQLAlchemyLedgerSnapshotService:
    """Inject the deliberately narrow engine stub at the test boundary."""

    return SQLAlchemyLedgerSnapshotService(cast(Engine, _EngineStub(connection)))


def _assert_unsupported_snapshot_scope(query: str, parameters: object) -> None:
    """Assert the account/period/query cleanup boundary passed to SQL."""

    assert "WITH scoped_owner_runs AS (" in query
    assert "SELECT DISTINCT artifact.ingestion_run_id FROM raw_artifact artifact" in query
    assert "artifact.account_id = :account_id" in query
    assert "artifact.period_key = :period_key" in query
    assert "artifact.flex_query_id = :flex_query_id" in query
    assert "snapshot.account_id = :account_id" in query
    assert "snapshot.ingestion_run_id IN (SELECT ingestion_run_id FROM scoped_owner_runs)" in query
    assert "NOT (snapshot.report_date_local = ANY(CAST(:supported_report_dates AS date[])))" in query
    assert parameters == {
        "account_id": "U1",
        "period_key": "2026-02-20",
        "flex_query_id": "query",
        "supported_report_dates": ["2026-02-19"],
    }


def test_unsupported_snapshot_cleanup_is_account_period_query_scoped() -> None:
    """Discover only unsupported snapshots owned by the requested replay scope."""

    connection = _ConnectionStub(rows=[{
        "report_date_local": date(2026, 2, 21),
        "row_count": 44,
    }])
    repository = _repository(connection)

    candidates = repository.db_pnl_snapshot_daily_unsupported_list(
        account_id="U1",
        period_key="2026-02-20",
        flex_query_id="query",
        supported_report_dates=("2026-02-19",),
    )

    assert candidates == [SnapshotCleanupCandidate(date(2026, 2, 21), 44)]
    _assert_unsupported_snapshot_scope(
        connection.executed_queries[0],
        connection.executed_parameters[0],
    )


def test_unsupported_snapshot_delete_returns_scoped_row_count() -> None:
    """Return the count deleted from the requested replay scope only."""

    connection = _ConnectionStub(rowcount=44)
    repository = _repository(connection)

    deleted = repository.db_pnl_snapshot_daily_unsupported_delete(
        account_id="U1",
        period_key="2026-02-20",
        flex_query_id="query",
        supported_report_dates=("2026-02-19",),
    )

    assert deleted == 44
    query = connection.executed_queries[0]
    assert "DELETE FROM pnl_snapshot_daily snapshot" in query
    _assert_unsupported_snapshot_scope(query, connection.executed_parameters[0])


@pytest.mark.parametrize("method_name", [
    "db_pnl_snapshot_daily_unsupported_list",
    "db_pnl_snapshot_daily_unsupported_delete",
])
def test_unsupported_snapshot_cleanup_rejects_empty_supported_dates(method_name: str) -> None:
    """Prevent an upstream selection bug from deleting an entire replay scope."""

    connection = _ConnectionStub()
    repository = _repository(connection)

    with pytest.raises(ValueError, match="^supported_report_dates must not be empty$"):
        getattr(repository, method_name)(
            account_id="U1",
            period_key="2026-02-20",
            flex_query_id="query",
            supported_report_dates=(),
        )
    assert connection.executed_queries == []


def test_scope_lookup_uses_conid_currency_union_and_normalizes_ids() -> None:
    """Resolve either affected key and return UUID-backed identifiers as strings."""

    instrument_id = uuid4()
    connection = _ConnectionStub(rows=[{"instrument_id": instrument_id}])
    repository = _repository(connection)

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
    repository = _repository(connection)

    result = repository.db_ledger_instrument_currency_list((instrument_id,))

    assert result == ["GBP", "USD"]
    assert "instrument_id = ANY(CAST(:instrument_ids AS uuid[]))" in connection.executed_queries[0]
    assert connection.executed_parameters[0] == {"instrument_ids": [instrument_id]}


def test_instrument_asset_category_map_is_account_and_instrument_scoped() -> None:
    instrument_id = uuid4()
    connection = _ConnectionStub(rows=[{
        "instrument_id": instrument_id,
        "asset_category": "CASH",
    }])
    repository = _repository(connection)

    categories = repository.db_ledger_instrument_asset_category_map(
        "U1",
        (str(instrument_id),),
    )

    assert categories == {str(instrument_id): "CASH"}
    query = connection.executed_queries[0]
    assert "account_id = :account_id" in query
    assert "instrument_id = ANY(CAST(:instrument_ids AS uuid[]))" in query
    assert connection.executed_parameters[0] == {
        "account_id": "U1",
        "instrument_ids": [str(instrument_id)],
    }


def test_scoped_ledger_reads_apply_instrument_and_currency_filters() -> None:
    """Constrain every scoped ledger source query at the database boundary."""

    instrument_id = str(uuid4())
    connection = _ConnectionStub()
    repository = _repository(connection)

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
    instrument_parameters = connection.executed_parameters[0]
    currency_parameters = connection.executed_parameters[4]
    assert isinstance(instrument_parameters, dict)
    assert isinstance(currency_parameters, dict)
    assert instrument_parameters["instrument_ids"] == [instrument_id]
    assert currency_parameters["currencies"] == ["EUR", "USD"]


def test_trade_read_includes_asset_category_and_raw_multiplier() -> None:
    instrument_id = uuid4()
    event_trade_fill_id = uuid4()
    source_raw_record_id = uuid4()
    connection = _ConnectionStub(rows=[{
        "event_trade_fill_id": event_trade_fill_id,
        "account_id": "U1",
        "instrument_id": instrument_id,
        "source_raw_record_id": source_raw_record_id,
        "trade_timestamp_utc": datetime(2026, 8, 20, tzinfo=timezone.utc),
        "report_date_local": date(2026, 8, 20),
        "side": "SELL",
        "quantity": Decimal("2"),
        "price": Decimal("0.60"),
        "fees": None,
        "commission": None,
        "functional_currency": "USD",
        "currency": "USD",
        "transaction_id": "1",
        "net_cash": None,
        "net_cash_in_base": None,
        "fx_rate_to_base": None,
        "asset_category": "OPT",
        "multiplier": Decimal("100"),
        "close_price": Decimal("0"),
    }])
    repository = _repository(connection)

    trade = repository.db_ledger_trade_fill_list_for_account("U1")[0]

    assert trade.asset_category == "OPT"
    assert trade.multiplier == "100"
    assert trade.close_price == "0"
    query = connection.executed_queries[0]
    assert "JOIN instrument i" in query
    assert "source_payload->>'multiplier'" in query


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
    repository = _repository(connection)

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
    repository = _repository(connection)
    row = repository.db_ledger_open_position_valuation_list_for_run(
        "U1", str(uuid4())
    )[0]
    assert row.mark_price is None
    assert row.cost_basis_money is None
    assert row.broker_unrealized_pnl is None
    assert row.fx_rate_to_base is None
    assert row.multiplier is None


def test_open_position_query_normalizes_flex_numeric_text() -> None:
    connection = _ConnectionStub()
    repository = _repository(connection)

    repository.db_ledger_open_position_valuation_list_for_run(
        "U1",
        str(uuid4()),
    )

    query = connection.executed_queries[0]
    assert (
        "REPLACE(BTRIM(COALESCE(rr.source_payload->>'position', '')), ',', '')::numeric "
        "AS position_qty"
    ) in query
    for field in (
        "markPrice",
        "costBasisMoney",
        "fifoPnlUnrealized",
        "fxRateToBase",
        "multiplier",
    ):
        assert f"BTRIM(COALESCE(rr.source_payload->>'{field}', '')) IN ('', '-', '--', 'N/A')" in query
        assert f"REPLACE(BTRIM(rr.source_payload->>'{field}'), ',', '')::numeric" in query


def test_scoped_lot_reconciliation_closes_and_replaces_only_selected_instruments() -> None:
    """Prevent an unrelated lot request from entering scoped persistence."""

    selected_id = str(uuid4())
    unrelated_id = str(uuid4())
    connection = _ConnectionStub()
    repository = _repository(connection)
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
