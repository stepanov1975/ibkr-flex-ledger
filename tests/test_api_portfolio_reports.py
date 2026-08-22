"""Portfolio workflow and frozen CSV endpoint tests."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.application import create_api_application
from app.config import AppSettings
from app.db import InstrumentPnlReportRecord, LabelPnlReportRecord, LabelRecord, ReconciliationSourceRecord
from app.domain import HealthStatus


class _Health:
    def db_connection_label(self) -> str:
        return "test"

    def db_check_health(self) -> HealthStatus:
        return HealthStatus("ok", "ok")


class _Runs:
    def db_ingestion_run_list(self, limit, offset, sort_by="started_at_utc", sort_dir="desc"):
        _ = (limit, offset, sort_by, sort_dir)
        return []

    def db_ingestion_run_get_by_id(self, ingestion_run_id):
        _ = ingestion_run_id
        return None


class _Jobs:
    def job_supported_names(self):
        return ("ingestion_run",)

    def job_execute(self, job_name):
        return type("Result", (), {"job_name": job_name, "status": "success"})()


class _Portfolio:
    def __init__(self) -> None:
        self.instrument_id = uuid4()
        self.short_instrument_id = uuid4()
        self.closed_instrument_id = uuid4()
        self.missing_cost_instrument_id = uuid4()
        self.label_id = uuid4()
        self.created_label: LabelRecord | None = None

    def db_report_pnl_by_instrument(self, account_id, date_from, date_to, instrument_id):
        _ = (account_id, date_from, date_to, instrument_id)
        return [
            InstrumentPnlReportRecord(
                report_date_local=date(2026, 8, 21), instrument_id=self.instrument_id, conid="123", symbol="TEST",
                currency="USD", position_qty="2", cost_basis="100", realized_pnl="10", unrealized_pnl="5",
                total_pnl="15", provisional=False, unresolved_case_count=0,
            ),
            InstrumentPnlReportRecord(
                report_date_local=date(2026, 8, 21), instrument_id=self.short_instrument_id, conid="124",
                symbol="SHORT", currency="USD", position_qty="-2", cost_basis="-100", realized_pnl="0",
                unrealized_pnl="-5", total_pnl="-5", provisional=False, unresolved_case_count=0,
            ),
            InstrumentPnlReportRecord(
                report_date_local=date(2026, 8, 21), instrument_id=self.closed_instrument_id, conid="125",
                symbol="CLOSED", currency="USD", position_qty="0", cost_basis="0", realized_pnl="20",
                unrealized_pnl="0", total_pnl="20", provisional=False, unresolved_case_count=0,
            ),
            InstrumentPnlReportRecord(
                report_date_local=date(2026, 8, 21), instrument_id=self.missing_cost_instrument_id, conid="126",
                symbol="MISSING", currency="USD", position_qty="3", cost_basis=None, realized_pnl="0",
                unrealized_pnl="0", total_pnl="0", provisional=True, unresolved_case_count=0,
            ),
        ]

    def db_report_pnl_by_label(self, account_id, date_from, date_to, label_id):
        _ = (account_id, date_from, date_to, label_id)
        return [LabelPnlReportRecord(
            report_date_local=date(2026, 8, 21), label_id=self.label_id, label_name="Core", instrument_count=1,
            realized_pnl="10", unrealized_pnl="5", total_pnl="15", fees="1", withholding_tax="0",
            provisional=False,
        )]

    def db_report_portfolio_summary(self, account_id):
        _ = account_id
        return SimpleNamespace(
            report_date_local=date(2026, 8, 21),
            cash_balances=(SimpleNamespace(currency="EUR", amount=None), SimpleNamespace(currency="USD", amount="100")),
            transfer_summary_by_currency=(
                SimpleNamespace(
                    currency="ILS", net_transfers="800", gross_deposits="1000", gross_withdrawals="200"
                ),
                SimpleNamespace(currency="USD", net_transfers="50", gross_deposits="50", gross_withdrawals="0"),
            ),
            transfers=(
                SimpleNamespace(
                    report_date_local=date(2026, 8, 20), transfer_type="Withdrawal", amount="200",
                    currency="ILS", description="Bank withdrawal",
                ),
                SimpleNamespace(
                    report_date_local=date(2026, 8, 19), transfer_type="Deposit", amount="1000",
                    currency="ILS", description="Bank deposit",
                ),
            ),
            net_transfers_usd="274",
            estimated_net_liquidation_value_usd="300",
            total_profit_usd="26",
            profit_percent="9.48905109",
        )

    def db_reconciliation_missing_sections(self, account_id, date_from, date_to):
        _ = (account_id, date_from, date_to)
        return []

    def db_reconciliation_sources(self, account_id, date_from, date_to, instrument_id):
        _ = (account_id, date_from, date_to, instrument_id)
        return [ReconciliationSourceRecord(
            report_date_local=date(2026, 8, 21), instrument_id=self.instrument_id, conid="123", symbol="TEST",
            currency="USD", position_qty="2", realized_pnl="10", unrealized_pnl="5", fees="1",
            withholding_tax="0", broker_position_qty="2", broker_realized_pnl="10",
            broker_unrealized_pnl="5", broker_fees="1", broker_withholding_tax="0", source_event_id=uuid4(),
            source_raw_record_id=uuid4(), provisional=False,
        )]

    def db_label_list(self):
        return [] if self.created_label is None else [self.created_label]

    def db_label_create(self, name, color):
        now = datetime.now(timezone.utc)
        self.created_label = LabelRecord(self.label_id, name, color, now, now)
        return self.created_label


def _client(repository: _Portfolio) -> TestClient:
    settings = AppSettings(
        environment_name="test", database_url="postgresql+psycopg://x:x@localhost/x", account_id="U1",
        ibkr_flex_token="token", ibkr_flex_query_id="query",
    )
    app = create_api_application(settings, _Health(), _Runs(), _Jobs(), portfolio_repository=repository)
    return TestClient(app)


def test_report_csv_v1_column_order_is_exact() -> None:
    """Keep by-instrument and by-label CSV headers stable."""

    client = _client(_Portfolio())
    instrument = client.get("/reports/pnl/by-instrument?format=csv")
    label = client.get("/reports/pnl/by-label?format=csv")

    assert instrument.status_code == 200
    assert instrument.headers["x-schema-version"] == "v1"
    assert instrument.text.splitlines()[0] == "report_date_local,instrument_id,conid,symbol,currency,position_qty,cost_basis,realized_pnl,unrealized_pnl,total_pnl,provisional"
    assert label.text.splitlines()[0] == "report_date_local,label_id,label_name,instrument_count,realized_pnl,unrealized_pnl,total_pnl,fees,withholding_tax,provisional"


def test_report_json_uses_boolean_status_fields() -> None:
    """Keep JSON booleans typed while CSV continues using text values."""

    client = _client(_Portfolio())

    instrument = client.get("/reports/pnl/by-instrument").json()["items"][0]
    reconciliation = client.get("/reports/reconciliation/diff").json()["items"][0]

    assert instrument["provisional"] is False
    assert reconciliation["within_tolerance"] is True
    assert reconciliation["provisional"] is False


def test_pnl_json_derives_cost_and_last_day_value_for_open_positions() -> None:
    """Calculate signed position values while leaving closed-position values unavailable."""

    items = _client(_Portfolio()).get("/reports/pnl/by-instrument").json()["items"]

    assert [
        {
            "symbol": item["symbol"],
            "average_cost": item["average_cost"],
            "total_cost": item["total_cost"],
            "last_day_value": item["last_day_value"],
        }
        for item in items
    ] == [
        {"symbol": "TEST", "average_cost": "50", "total_cost": "100", "last_day_value": "105"},
        {"symbol": "SHORT", "average_cost": "50", "total_cost": "-100", "last_day_value": "-105"},
        {"symbol": "CLOSED", "average_cost": None, "total_cost": None, "last_day_value": None},
        {"symbol": "MISSING", "average_cost": None, "total_cost": None, "last_day_value": None},
    ]


def test_portfolio_summary_exposes_cash_transfers_and_profit_contract() -> None:
    """Return portfolio metrics and original-currency transfers in one report."""

    response = _client(_Portfolio()).get("/reports/portfolio-summary")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "v1",
        "report_date_local": "2026-08-21",
        "cash_balances": [
            {"currency": "EUR", "amount": None},
            {"currency": "USD", "amount": "100"},
        ],
        "transfer_summary_by_currency": [
            {"currency": "ILS", "net_transfers": "800", "gross_deposits": "1000", "gross_withdrawals": "200"},
            {"currency": "USD", "net_transfers": "50", "gross_deposits": "50", "gross_withdrawals": "0"},
        ],
        "transfers": [
            {
                "report_date_local": "2026-08-20", "type": "Withdrawal", "amount": "200", "currency": "ILS",
                "description": "Bank withdrawal",
            },
            {
                "report_date_local": "2026-08-19", "type": "Deposit", "amount": "1000", "currency": "ILS",
                "description": "Bank deposit",
            },
        ],
        "net_transfers_usd": "274",
        "estimated_net_liquidation_value_usd": "300",
        "total_profit_usd": "26",
        "profit_percent": "9.48905109",
    }


def test_reconciliation_csv_contains_frozen_diff_contract() -> None:
    """Expose all tolerance and provenance fields in exact order."""

    response = _client(_Portfolio()).get("/reports/reconciliation/diff?format=csv")

    assert response.status_code == 200
    assert response.text.splitlines()[0] == "report_date_local,instrument_id,conid,symbol,metric,broker_value,economic_value,abs_diff,rel_diff,tolerance_abs,tolerance_rel,within_tolerance,formula_context,source_event_id,source_raw_record_id,provisional"
    assert len(response.text.splitlines()) == 6


def test_label_create_is_available_through_portfolio_api() -> None:
    """Create and list a label through the browser-facing workflow."""

    client = _client(_Portfolio())
    created = client.post("/labels", json={"name": "Core", "color": "#68d5b4"})
    listed = client.get("/labels")

    assert created.status_code == 201
    assert listed.json()["items"][0]["name"] == "Core"
