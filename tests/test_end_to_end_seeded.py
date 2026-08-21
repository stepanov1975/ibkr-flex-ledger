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
        assert first_result.status == "success"
        with engine.begin() as connection:
            initial_trade = dict(
                connection.execute(
                    text(
                        "SELECT event_trade_fill_id, ingestion_run_id, source_raw_record_id, price "
                        "FROM event_trade_fill WHERE account_id='SEEDED_ACCOUNT' "
                        "AND ib_exec_id='SEED-EXEC-1'"
                    )
                ).mappings().one()
            )
            initial_snapshot = dict(
                connection.execute(
                    text(
                        "SELECT position_qty, cost_basis, realized_pnl, unrealized_pnl, total_pnl, fees "
                        "FROM pnl_snapshot_daily WHERE account_id='SEEDED_ACCOUNT'"
                    )
                ).mappings().one()
            )
            initial_lot = dict(
                connection.execute(
                    text(
                        "SELECT position_lot_id, open_event_trade_fill_id, open_quantity, "
                        "remaining_quantity, open_price, cost_basis_open FROM position_lot "
                        "WHERE account_id='SEEDED_ACCOUNT' AND status='open'"
                    )
                ).mappings().one()
            )
            connection.execute(
                text(
                    "UPDATE position_lot SET open_quantity=999 "
                    "WHERE position_lot_id=:position_lot_id"
                ),
                {"position_lot_id": initial_lot["position_lot_id"]},
            )
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
            corrected_trade = connection.execute(
                text(
                    "SELECT event_trade_fill_id, ingestion_run_id, source_raw_record_id, price "
                    "FROM event_trade_fill "
                    "WHERE account_id='SEEDED_ACCOUNT' AND ib_exec_id='SEED-EXEC-1'"
                )
            ).mappings().one()
            corrected_snapshot = connection.execute(
                text(
                    "SELECT position_qty, cost_basis, realized_pnl, unrealized_pnl, total_pnl, fees "
                    "FROM pnl_snapshot_daily WHERE account_id='SEEDED_ACCOUNT'"
                )
            ).mappings().one()
            corrected_lot = connection.execute(
                text(
                    "SELECT position_lot_id, open_event_trade_fill_id, open_quantity, "
                    "remaining_quantity, open_price, cost_basis_open FROM position_lot "
                    "WHERE account_id='SEEDED_ACCOUNT' AND status='open'"
                )
            ).mappings().one()
            assert corrected_trade["price"] == Decimal("111")
            assert corrected_trade["event_trade_fill_id"] == initial_trade["event_trade_fill_id"]
            assert corrected_trade["ingestion_run_id"] == initial_trade["ingestion_run_id"]
            assert corrected_trade["source_raw_record_id"] == initial_trade["source_raw_record_id"]
            assert dict(corrected_snapshot) == {
                "position_qty": Decimal("2"),
                "cost_basis": Decimal("223"),
                "realized_pnl": Decimal("0"),
                "unrealized_pnl": Decimal("-3"),
                "total_pnl": Decimal("-3"),
                "fees": Decimal("1"),
            }
            assert dict(corrected_snapshot) != initial_snapshot
            assert corrected_lot["position_lot_id"] == initial_lot["position_lot_id"]
            assert (
                corrected_lot["open_event_trade_fill_id"]
                == initial_lot["open_event_trade_fill_id"]
            )
            assert corrected_lot["open_quantity"] == Decimal("2")
            assert corrected_lot["remaining_quantity"] == Decimal("2")
            assert corrected_lot["open_price"] == Decimal("111")
            assert corrected_lot["cost_basis_open"] == Decimal("223")
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


def test_postgresql_fx_only_scope_rebuild_preserves_unrelated_instrument_state() -> None:
    """Rebuild an FX-affected instrument without touching unrelated lots or snapshots."""

    base_url = _reachable_database_url()
    database_name = f"test_fx_scope_{uuid.uuid4().hex[:10]}"
    admin_url = _database_url(base_url, "postgres")
    test_database_url = _database_url(base_url, database_name)
    previous_database_url = os.environ.get("DATABASE_URL")
    _create_database(admin_url, database_name)
    os.environ["DATABASE_URL"] = test_database_url
    engine = None
    try:
        command.upgrade(Config("alembic.ini"), "head")
        engine = db_create_engine(test_database_url)
        run_id = uuid.uuid4()
        eur_instrument_id = uuid.uuid4()
        gbp_instrument_id = uuid.uuid4()
        eur_trade_raw_id = uuid.uuid4()
        gbp_trade_raw_id = uuid.uuid4()
        eur_fx_raw_id = uuid.uuid4()
        gbp_fx_raw_id = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ingestion_run ("
                    "ingestion_run_id, account_id, run_type, status, period_key, flex_query_id, "
                    "report_date_local, started_at_utc, ended_at_utc) VALUES ("
                    ":run_id, 'U_FX_SCOPE', 'manual', 'success', '2026-08', 'query', "
                    "DATE '2026-08-21', now(), now())"
                ),
                {"run_id": run_id},
            )
            connection.execute(
                text(
                    "INSERT INTO instrument ("
                    "instrument_id, account_id, conid, symbol, asset_category, currency) VALUES ("
                    ":instrument_id, 'U_FX_SCOPE', :conid, :symbol, 'STK', :currency)"
                ),
                [
                    {
                        "instrument_id": eur_instrument_id,
                        "conid": "100",
                        "symbol": "EUR_STOCK",
                        "currency": "EUR",
                    },
                    {
                        "instrument_id": gbp_instrument_id,
                        "conid": "200",
                        "symbol": "GBP_STOCK",
                        "currency": "GBP",
                    },
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO raw_record ("
                    "raw_record_id, ingestion_run_id, account_id, period_key, flex_query_id, "
                    "payload_sha256, report_date_local, section_name, source_row_ref, source_payload) VALUES ("
                    ":raw_record_id, :run_id, 'U_FX_SCOPE', '2026-08', 'query', :sha, "
                    "DATE '2026-08-21', :section_name, :source_row_ref, "
                    "jsonb_build_object('closePrice', CAST(:close_price AS text)))"
                ),
                [
                    {
                        "raw_record_id": eur_trade_raw_id,
                        "run_id": run_id,
                        "sha": "eur-trade",
                        "section_name": "Trades",
                        "source_row_ref": "trade-eur",
                        "close_price": "12",
                    },
                    {
                        "raw_record_id": gbp_trade_raw_id,
                        "run_id": run_id,
                        "sha": "gbp-trade",
                        "section_name": "Trades",
                        "source_row_ref": "trade-gbp",
                        "close_price": "25",
                    },
                    {
                        "raw_record_id": eur_fx_raw_id,
                        "run_id": run_id,
                        "sha": "eur-fx",
                        "section_name": "ConversionRates",
                        "source_row_ref": "fx-eur",
                        "close_price": "",
                    },
                    {
                        "raw_record_id": gbp_fx_raw_id,
                        "run_id": run_id,
                        "sha": "gbp-fx",
                        "section_name": "ConversionRates",
                        "source_row_ref": "fx-gbp",
                        "close_price": "",
                    },
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO event_trade_fill ("
                    "event_trade_fill_id, account_id, instrument_id, ingestion_run_id, "
                    "source_raw_record_id, ib_exec_id, transaction_id, trade_timestamp_utc, "
                    "report_date_local, side, quantity, price, commission, fees, currency, "
                    "functional_currency) VALUES ("
                    ":event_id, 'U_FX_SCOPE', :instrument_id, :run_id, :raw_record_id, :exec_id, "
                    ":transaction_id, TIMESTAMPTZ '2026-08-21 12:00:00+00', DATE '2026-08-21', "
                    "'BUY', 1, :price, 0, 0, :currency, 'USD')"
                ),
                [
                    {
                        "event_id": uuid.uuid4(),
                        "instrument_id": eur_instrument_id,
                        "run_id": run_id,
                        "raw_record_id": eur_trade_raw_id,
                        "exec_id": "EUR-EXEC",
                        "transaction_id": "1",
                        "price": "10",
                        "currency": "EUR",
                    },
                    {
                        "event_id": uuid.uuid4(),
                        "instrument_id": gbp_instrument_id,
                        "run_id": run_id,
                        "raw_record_id": gbp_trade_raw_id,
                        "exec_id": "GBP-EXEC",
                        "transaction_id": "2",
                        "price": "20",
                        "currency": "GBP",
                    },
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO event_fx ("
                    "account_id, ingestion_run_id, source_raw_record_id, transaction_id, "
                    "report_date_local, currency, functional_currency, fx_rate, fx_source) VALUES ("
                    "'U_FX_SCOPE', :run_id, :raw_record_id, :transaction_id, DATE '2026-08-21', "
                    ":currency, 'USD', :fx_rate, 'seeded')"
                ),
                [
                    {
                        "run_id": run_id,
                        "raw_record_id": eur_fx_raw_id,
                        "transaction_id": "FX-EUR",
                        "currency": "EUR",
                        "fx_rate": "1",
                    },
                    {
                        "run_id": run_id,
                        "raw_record_id": gbp_fx_raw_id,
                        "transaction_id": "FX-GBP",
                        "currency": "GBP",
                        "fx_rate": "2",
                    },
                ],
            )

        snapshot_repository = SQLAlchemyLedgerSnapshotService(engine)
        snapshot_service = StockLedgerSnapshotService(snapshot_repository)
        initial_result = snapshot_service.ledger_snapshot_build_and_persist(
            account_id="U_FX_SCOPE",
            ingestion_run_id=str(run_id),
            report_date_local="2026-08-21",
        )
        assert initial_result.snapshot_row_count == 2
        assert initial_result.position_lot_row_count == 2

        with engine.connect() as connection:
            initial_snapshots = {
                row["instrument_id"]: dict(row)
                for row in connection.execute(
                    text(
                        "SELECT * FROM pnl_snapshot_daily WHERE account_id='U_FX_SCOPE' "
                        "ORDER BY instrument_id"
                    )
                ).mappings().all()
            }
            initial_lots = {
                row["instrument_id"]: dict(row)
                for row in connection.execute(
                    text(
                        "SELECT * FROM position_lot WHERE account_id='U_FX_SCOPE' "
                        "ORDER BY instrument_id"
                    )
                ).mappings().all()
            }

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE event_fx SET fx_rate=1.5 "
                    "WHERE account_id='U_FX_SCOPE' AND currency='EUR'"
                )
            )

        scoped_result = snapshot_service.ledger_snapshot_build_and_persist(
            account_id="U_FX_SCOPE",
            ingestion_run_id=str(run_id),
            report_date_local="2026-08-21",
            affected_conids=frozenset(),
            affected_currencies=frozenset({"EUR"}),
        )
        assert scoped_result.snapshot_row_count == 1
        assert scoped_result.position_lot_row_count == 1

        with engine.connect() as connection:
            final_snapshots = {
                row["instrument_id"]: dict(row)
                for row in connection.execute(
                    text(
                        "SELECT * FROM pnl_snapshot_daily WHERE account_id='U_FX_SCOPE' "
                        "ORDER BY instrument_id"
                    )
                ).mappings().all()
            }
            final_lots = {
                row["instrument_id"]: dict(row)
                for row in connection.execute(
                    text(
                        "SELECT * FROM position_lot WHERE account_id='U_FX_SCOPE' "
                        "ORDER BY instrument_id"
                    )
                ).mappings().all()
            }

        assert final_snapshots[gbp_instrument_id] == initial_snapshots[gbp_instrument_id]
        assert final_lots[gbp_instrument_id] == initial_lots[gbp_instrument_id]
        assert final_snapshots[eur_instrument_id]["cost_basis"] == Decimal("15")
        assert final_snapshots[eur_instrument_id]["unrealized_pnl"] == Decimal("3")
        assert final_snapshots[eur_instrument_id] != initial_snapshots[eur_instrument_id]
        assert final_lots[eur_instrument_id]["open_price"] == Decimal("15")
        assert final_lots[eur_instrument_id]["cost_basis_open"] == Decimal("15")
        assert final_lots[eur_instrument_id] != initial_lots[eur_instrument_id]
    finally:
        if engine is not None:
            engine.dispose()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        _drop_database(admin_url, database_name)
