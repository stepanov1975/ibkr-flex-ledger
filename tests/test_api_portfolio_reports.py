"""Portfolio workflow and frozen CSV endpoint tests."""

from datetime import date, datetime, timezone
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
        self.label_id = uuid4()
        self.created_label: LabelRecord | None = None

    def db_report_pnl_by_instrument(self, account_id, date_from, date_to, instrument_id):
        _ = (account_id, date_from, date_to, instrument_id)
        return [InstrumentPnlReportRecord(
            report_date_local=date(2026, 8, 21), instrument_id=self.instrument_id, conid="123", symbol="TEST",
            currency="USD", position_qty="2", cost_basis="100", realized_pnl="10", unrealized_pnl="5",
            total_pnl="15", provisional=False, unresolved_case_count=0,
        )]

    def db_report_pnl_by_label(self, account_id, date_from, date_to, label_id):
        _ = (account_id, date_from, date_to, label_id)
        return [LabelPnlReportRecord(
            report_date_local=date(2026, 8, 21), label_id=self.label_id, label_name="Core", instrument_count=1,
            realized_pnl="10", unrealized_pnl="5", total_pnl="15", fees="1", withholding_tax="0",
            provisional=False,
        )]

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
