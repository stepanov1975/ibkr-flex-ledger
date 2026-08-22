"""Portfolio-summary aggregation tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import TracebackType
from typing import Literal, cast

from sqlalchemy import Engine

from app.db import (
    CashBalanceReportRecord,
    PortfolioSummaryReportRecord,
    TransferReportRecord,
    TransferSummaryReportRecord,
)
from app.db.portfolio import SQLAlchemyPortfolioService


class _ResultStub:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _ResultStub:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _ConnectionStub:
    def __init__(
        self,
        cash_rows: list[dict[str, object]],
        position_rows: list[dict[str, object]],
        transfer_rows: list[dict[str, object]],
    ) -> None:
        self._cash_rows = cash_rows
        self._position_rows = position_rows
        self._transfer_rows = transfer_rows
        self.executed_queries: list[str] = []

    def __enter__(self) -> _ConnectionStub:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        _ = (exc_type, exc_value, traceback)
        return False

    def execute(self, statement: object, parameters: dict[str, object]) -> _ResultStub:
        assert parameters == {"account_id": "U1"}
        query = str(statement)
        self.executed_queries.append(query)
        if "section_name='OpenPositions'" in query:
            return _ResultStub(self._position_rows)
        if "cash_action='Deposits/Withdrawals'" in query:
            return _ResultStub(self._transfer_rows)
        if "section_name='CashReport'" in query:
            return _ResultStub(self._cash_rows)
        raise AssertionError(f"Unexpected portfolio-summary query: {query}")


class _EngineStub:
    def __init__(self, connection: _ConnectionStub) -> None:
        self._connection = connection

    def connect(self) -> _ConnectionStub:
        return self._connection


def _repository(connection: _ConnectionStub) -> SQLAlchemyPortfolioService:
    return SQLAlchemyPortfolioService(cast(Engine, _EngineStub(connection)))


def _cash_rows() -> list[dict[str, object]]:
    return [
        {"report_date_local": date(2026, 8, 21), "currency": "BASE_SUMMARY", "ending_cash": Decimal("100")},
        {"report_date_local": date(2026, 8, 21), "currency": "EUR", "ending_cash": Decimal("25")},
        {"report_date_local": date(2026, 8, 21), "currency": "USD", "ending_cash": Decimal("100")},
    ]


def _position_rows(
    *, position_value_usd: Decimal | None = Decimal("200"), missing_value_count: int = 0
) -> list[dict[str, object]]:
    return [{
        "report_date_local": date(2026, 8, 21),
        "open_positions_present": True,
        "position_value_usd": position_value_usd,
        "missing_value_count": missing_value_count,
    }]


def _transfer_rows() -> list[dict[str, object]]:
    return [
        {
            "report_date_local": date(2026, 8, 20), "amount": Decimal("-200"), "amount_in_base": None,
            "currency": "ILS", "functional_currency": "USD", "fx_rate_to_base": Decimal("0.28"),
            "description": "Bank withdrawal",
        },
        {
            "report_date_local": date(2026, 8, 19), "amount": Decimal("1000"), "amount_in_base": None,
            "currency": "ILS", "functional_currency": "USD", "fx_rate_to_base": Decimal("0.28"),
            "description": "Bank deposit",
        },
        {
            "report_date_local": date(2026, 8, 18), "amount": Decimal("50"), "amount_in_base": None,
            "currency": "USD", "functional_currency": "USD", "fx_rate_to_base": None,
            "description": None,
        },
    ]


def test_portfolio_summary_calculates_cash_transfers_nlv_and_profit() -> None:
    """Aggregate signed transfers while displaying original-currency magnitudes."""

    connection = _ConnectionStub(_cash_rows(), _position_rows(), _transfer_rows())
    repository = _repository(connection)

    method = getattr(repository, "db_report_portfolio_summary", None)
    assert method is not None, "portfolio summary repository method is missing"
    summary = method("U1")

    assert summary == PortfolioSummaryReportRecord(
        report_date_local=date(2026, 8, 21),
        cash_balances=(CashBalanceReportRecord("EUR", "25"), CashBalanceReportRecord("USD", "100")),
        transfer_summary_by_currency=(
            TransferSummaryReportRecord("ILS", "800", "1000", "200"),
            TransferSummaryReportRecord("USD", "50", "50", "0"),
        ),
        transfers=(
            TransferReportRecord(date(2026, 8, 20), "Withdrawal", "200", "ILS", "Bank withdrawal"),
            TransferReportRecord(date(2026, 8, 19), "Deposit", "1000", "ILS", "Bank deposit"),
            TransferReportRecord(date(2026, 8, 18), "Deposit", "50", "USD", None),
        ),
        net_transfers_usd="274.00",
        estimated_net_liquidation_value_usd="300",
        total_profit_usd="26.00",
        profit_percent="9.48905109",
    )


def test_portfolio_summary_keeps_currency_totals_when_transfer_fx_is_missing() -> None:
    """Avoid presenting partial USD transfer and profit totals as complete."""

    transfers = [{
        "report_date_local": date(2026, 8, 19), "amount": Decimal("10"), "amount_in_base": None,
        "currency": "EUR", "functional_currency": "USD", "fx_rate_to_base": None,
        "description": "Deposit without FX",
    }]
    repository = _repository(_ConnectionStub(_cash_rows(), _position_rows(), transfers))

    method = getattr(repository, "db_report_portfolio_summary", None)
    assert method is not None, "portfolio summary repository method is missing"
    summary = method("U1")

    assert summary.transfer_summary_by_currency == (TransferSummaryReportRecord("EUR", "10", "10", "0"),)
    assert summary.net_transfers_usd is None
    assert summary.estimated_net_liquidation_value_usd == "300"
    assert summary.total_profit_usd is None
    assert summary.profit_percent is None


def test_portfolio_summary_marks_nlv_and_profit_unavailable_for_missing_position_value() -> None:
    """Do not calculate NLV when any current position lacks USD valuation data."""

    repository = _repository(
        _ConnectionStub(_cash_rows(), _position_rows(position_value_usd=None, missing_value_count=1), _transfer_rows())
    )

    method = getattr(repository, "db_report_portfolio_summary", None)
    assert method is not None, "portfolio summary repository method is missing"
    summary = method("U1")

    assert summary.net_transfers_usd == "274.00"
    assert summary.estimated_net_liquidation_value_usd is None
    assert summary.total_profit_usd is None
    assert summary.profit_percent is None


def test_portfolio_summary_treats_present_empty_positions_as_zero_value() -> None:
    """Calculate cash-only NLV when the latest OpenPositions section is valid and empty."""

    empty_positions = [{
        "report_date_local": date(2026, 8, 21),
        "open_positions_present": True,
        "position_value_usd": Decimal("0"),
        "missing_value_count": 0,
    }]
    connection = _ConnectionStub(_cash_rows(), empty_positions, [])
    repository = _repository(connection)

    method = getattr(repository, "db_report_portfolio_summary", None)
    assert method is not None, "portfolio summary repository method is missing"
    summary = method("U1")

    assert summary.estimated_net_liquidation_value_usd == "100"
    position_query = connection.executed_queries[1]
    assert "row_present=1 AND (position_value IS NULL OR fx_rate IS NULL)" in position_query
    assert "raw.raw_artifact_id=selected.raw_artifact_id" in position_query
    assert "NOT IN ('CASH', 'FX')" in position_query
    assert "section_name='CashReport'" not in position_query


def test_portfolio_summary_keeps_currency_with_missing_cash_balance() -> None:
    """Expose a reported currency as unavailable instead of silently dropping it."""

    cash_rows = _cash_rows()
    cash_rows[1]["ending_cash"] = None
    repository = _repository(_ConnectionStub(cash_rows, _position_rows(), []))

    method = getattr(repository, "db_report_portfolio_summary", None)
    assert method is not None, "portfolio summary repository method is missing"
    summary = method("U1")

    assert summary.cash_balances == (
        CashBalanceReportRecord("EUR", None),
        CashBalanceReportRecord("USD", "100"),
    )


def test_portfolio_summary_rejects_nonpositive_transfer_fx() -> None:
    """Treat a zero or negative IBKR transfer FX rate as unavailable."""

    for invalid_rate in (Decimal("0"), Decimal("-0.28")):
        transfers = [{
            "report_date_local": date(2026, 8, 19), "amount": Decimal("10"), "amount_in_base": None,
            "currency": "EUR", "functional_currency": "USD", "fx_rate_to_base": invalid_rate,
            "description": "Deposit with invalid FX",
        }]
        repository = _repository(_ConnectionStub(_cash_rows(), _position_rows(), transfers))

        method = getattr(repository, "db_report_portfolio_summary", None)
        assert method is not None, "portfolio summary repository method is missing"
        summary = method("U1")

        assert summary.net_transfers_usd is None
        assert summary.total_profit_usd is None
