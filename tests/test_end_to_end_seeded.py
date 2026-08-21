"""Seeded PostgreSQL walkthrough from Flex ingestion to auditable reports."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import cast

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

from app.adapters import AdapterFetchResult
from app.api import create_api_application
from app.config import AppSettings
from app.db import (
    SQLAlchemyCanonicalPersistenceService,
    SQLAlchemyDatabaseHealthService,
    SQLAlchemyIngestionRunService,
    SQLAlchemyLedgerSnapshotService,
    SQLAlchemyPortfolioService,
    SQLAlchemyRawPersistenceService,
    db_create_engine,
)
from app.jobs import IngestionJobOrchestrator, IngestionOrchestratorConfig
from app.ledger import StockLedgerSnapshotService


_SEEDED_PAYLOAD = b"""<FlexQueryResponse><FlexStatements count="1">
<FlexStatement reportDate="20260821">
  <Trades><Trade transactionID="9001" ibExecID="SEED-EXEC-1" conid="900001" symbol="SEED"
    assetCategory="STK" buySell="BUY" quantity="2" tradePrice="100" closePrice="110"
    currency="USD" reportDate="20260821" dateTime="20260821;120000"
    ibCommission="1" commission="1" fees="0" fifoPnlRealized="0" fxRateToBase="1" /></Trades>
  <OpenPositions><OpenPosition conid="900001" symbol="SEED" assetCategory="STK" currency="USD"
    reportDate="20260821" position="2" markPrice="110" fifoPnlUnrealized="20" /></OpenPositions>
  <CashTransactions><CashTransaction transactionID="9002" conid="900001" symbol="SEED"
    assetCategory="STK" type="DIV" amount="0" amountInBase="0" withholdingTax="0" fees="0"
    currency="USD" reportDate="20260821" dateTime="20260821;130000" /></CashTransactions>
  <CorporateActions />
  <ConversionRates />
  <SecuritiesInfo />
  <AccountInformation />
  <MTMPerformanceSummaryInBase />
  <FIFOPerformanceSummaryInBase />
</FlexStatement></FlexStatements></FlexQueryResponse>"""


class _SeededAdapter:
    def __init__(self, payload_bytes: bytes = _SEEDED_PAYLOAD) -> None:
        self.payload_bytes = payload_bytes

    def adapter_source_name(self) -> str:
        return "seeded-test"

    def adapter_fetch_report(self, query_id: str) -> AdapterFetchResult:
        assert query_id == "seeded-query"
        return AdapterFetchResult(
            run_reference="seeded-report",
            payload_bytes=self.payload_bytes,
            stage_timeline=[{"stage": "request", "status": "completed"}],
        )


def _database_url(base_url: str, database_name: str) -> str:
    parsed_url: URL = make_url(base_url)
    return str(parsed_url.set(database=database_name).render_as_string(hide_password=False))


def _reachable_database_url() -> str:
    candidates = [
        value
        for value in (
            os.getenv("DATABASE_URL"),
            "postgresql+psycopg://postgres:postgres@localhost:5433/postgres",
            "postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
            "postgresql+psycopg:///postgres",
        )
        if value
    ]
    for candidate in candidates:
        engine = create_engine(candidate, connect_args={"connect_timeout": 1})
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return candidate
        except SQLAlchemyError:
            continue
        finally:
            engine.dispose()
    pytest.skip("No reachable PostgreSQL URL for seeded end-to-end test")
    return ""


def _create_database(admin_url: str, database_name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        engine.dispose()


def _drop_database(admin_url: str, database_name: str) -> None:
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:database_name AND pid<>pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
    finally:
        engine.dispose()


def _completed_details(run: Mapping[str, object], stage: str) -> dict[str, object]:
    diagnostics = run["diagnostics"]
    assert isinstance(diagnostics, list)
    details = next(
        event["details"]
        for event in diagnostics
        if isinstance(event, dict)
        and event.get("stage") == stage
        and event.get("status") == "completed"
    )
    assert isinstance(details, dict)
    return cast(dict[str, object], details)


def test_seeded_ingestion_duplicate_skips_semantic_work_and_correction_is_incremental() -> None:
    """Prove duplicate and corrected Flex payloads retain auditable incremental results."""

    base_url = _reachable_database_url()
    database_name = f"test_seeded_e2e_{uuid.uuid4().hex[:10]}"
    admin_url = _database_url(base_url, "postgres")
    test_database_url = _database_url(base_url, database_name)
    previous_database_url = os.environ.get("DATABASE_URL")
    _create_database(admin_url, database_name)
    os.environ["DATABASE_URL"] = test_database_url
    engine = None

    try:
        command.upgrade(Config("alembic.ini"), "head")
        engine = db_create_engine(test_database_url)
        ingestion_repository = SQLAlchemyIngestionRunService(engine)
        raw_repository = SQLAlchemyRawPersistenceService(engine)
        canonical_repository = SQLAlchemyCanonicalPersistenceService(engine)
        snapshot_repository = SQLAlchemyLedgerSnapshotService(engine)
        portfolio_repository = SQLAlchemyPortfolioService(engine)
        seeded_adapter = _SeededAdapter()
        orchestrator = IngestionJobOrchestrator(
            ingestion_repository=ingestion_repository,
            raw_persistence_repository=raw_repository,
            flex_adapter=seeded_adapter,
            config=IngestionOrchestratorConfig(
                account_id="SEEDED_ACCOUNT",
                flex_query_id="seeded-query",
                reconciliation_enabled=True,
            ),
            canonical_repository=canonical_repository,
            snapshot_service=StockLedgerSnapshotService(snapshot_repository),
        )

        first_result = orchestrator.job_execute("ingestion_run")
        duplicate_result = orchestrator.job_execute("ingestion_run")
        seeded_adapter.payload_bytes = _SEEDED_PAYLOAD.replace(
            b'tradePrice="100"', b'tradePrice="111"'
        )
        corrected_result = orchestrator.job_execute("ingestion_run")
        assert [first_result.status, duplicate_result.status, corrected_result.status] == [
            "success"
        ] * 3

        settings = AppSettings(
            environment_name="test",
            database_url=test_database_url,
            account_id="SEEDED_ACCOUNT",
            ibkr_flex_token="seeded-token",
            ibkr_flex_query_id="seeded-query",
        )
        application = create_api_application(
            settings=settings,
            db_health_service=SQLAlchemyDatabaseHealthService(engine),
            ingestion_repository=ingestion_repository,
            ingestion_orchestrator=orchestrator,
            snapshot_repository=snapshot_repository,
            portfolio_repository=portfolio_repository,
        )
        with TestClient(application) as client:
            pnl_response = client.get("/reports/pnl/by-instrument")
            assert pnl_response.status_code == 200
            pnl_items = pnl_response.json()["items"]
            assert len(pnl_items) == 1
            assert pnl_items[0]["symbol"] == "SEED"
            assert pnl_items[0]["report_date_local"] == "2026-08-21"

            reconciliation_response = client.get("/reports/reconciliation/diff")
            assert reconciliation_response.status_code == 200
            reconciliation_items = reconciliation_response.json()["items"]
            assert {item["metric"] for item in reconciliation_items} == {
                "fees",
                "position_qty",
                "realized_pnl",
                "unrealized_pnl",
                "withholding_tax",
            }

            provenance_response = client.get(
                "/reports/provenance",
                params={
                    "report_date_local": "2026-08-21",
                    "instrument_id": pnl_items[0]["instrument_id"],
                },
            )
            assert provenance_response.status_code == 200
            provenance_items = provenance_response.json()["items"]
            assert {item["event_type"] for item in provenance_items} == {"cashflow", "trade_fill"}
            assert all(item["source_raw_record_id"] for item in provenance_items)

        with engine.connect() as connection:
            runs = connection.execute(
                text(
                    "SELECT ingestion_run_id, diagnostics FROM ingestion_run "
                    "WHERE account_id='SEEDED_ACCOUNT' ORDER BY started_at_utc, ingestion_run_id"
                )
            ).mappings().all()
            raw_counts = [
                connection.execute(
                    text("SELECT count(*) FROM raw_record WHERE ingestion_run_id=:run_id"),
                    {"run_id": run["ingestion_run_id"]},
                ).scalar_one()
                for run in runs
            ]
            assert connection.execute(text("SELECT count(*) FROM raw_artifact")).scalar_one() == 2
            assert raw_counts[0] > 0
            assert raw_counts[1] == 0
            assert raw_counts[2] == raw_counts[0]
            corrected_price = connection.execute(
                text(
                    "SELECT price FROM event_trade_fill "
                    "WHERE account_id='SEEDED_ACCOUNT' AND ib_exec_id='SEED-EXEC-1'"
                )
            ).scalar_one()
            assert corrected_price == Decimal("111")
            assert (
                _completed_details(runs[1], "canonical_mapping")["canonical_skip_reason"]
                == "exact_duplicate_artifact"
            )
            assert (
                _completed_details(runs[2], "canonical_mapping")["canonical_input_row_count"] == 1
            )
            assert _completed_details(runs[2], "snapshot")["snapshot_scope_mode"] == "incremental"
            assert connection.execute(text("SELECT count(*) FROM pnl_snapshot_daily")).scalar_one() == 1
    finally:
        if engine is not None:
            engine.dispose()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _drop_database(admin_url, database_name)
