"""Real PostgreSQL regressions for failed ingestion, FX history and recovery replay."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text

from app.db import (
    SQLAlchemyCanonicalPersistenceService,
    SQLAlchemyIngestionRunService,
    SQLAlchemyLedgerSnapshotService,
    SQLAlchemyRawPersistenceService,
    db_create_engine,
)
from app.jobs import (
    CanonicalReprocessOrchestrator,
    CanonicalReprocessOrchestratorConfig,
    IngestionJobOrchestrator,
    IngestionOrchestratorConfig,
)
from app.ledger import StockLedgerSnapshotService
from test_end_to_end_seeded import (
    _SEEDED_PAYLOAD,
    _SeededAdapter,
    _create_database,
    _database_url,
    _drop_database,
    _reachable_database_url,
)


@pytest.fixture
def database(monkeypatch):
    admin_url = _database_url(_reachable_database_url(), "postgres")
    name = f"test_ingestion_integrity_{uuid4().hex[:10]}"
    url = _database_url(admin_url, name)
    _create_database(admin_url, name)
    engine = None
    try:
        monkeypatch.setenv("DATABASE_URL", url)
        command.upgrade(Config("alembic.ini"), "head")
        engine = db_create_engine(url)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        _drop_database(admin_url, name)


def _harness(engine, account="INTEGRITY"):
    adapter = _SeededAdapter()
    runs = SQLAlchemyIngestionRunService(engine)
    raw = SQLAlchemyRawPersistenceService(engine)
    canonical = SQLAlchemyCanonicalPersistenceService(engine)
    snapshots = SQLAlchemyLedgerSnapshotService(engine)
    service = StockLedgerSnapshotService(snapshots)
    orchestrator = IngestionJobOrchestrator(
        ingestion_repository=runs,
        raw_persistence_repository=raw,
        canonical_repository=canonical,
        snapshot_service=service,
        flex_adapter=adapter,
        config=IngestionOrchestratorConfig(account_id=account, flex_query_id="seeded-query"),
    )
    return orchestrator, adapter, raw, canonical, service, snapshots, runs


def _replay(harness, period):
    _, _, _, canonical, service, snapshots, runs = harness
    return CanonicalReprocessOrchestrator(
        raw_read_repository=canonical,
        canonical_persistence_repository=canonical,
        snapshot_service=service,
        snapshot_repository=snapshots,
        ingestion_repository=runs,
        config=CanonicalReprocessOrchestratorConfig(
            account_id="INTEGRITY", period_key=period, flex_query_id="seeded-query"
        ),
    ).job_execute("reprocess_run")


@pytest.mark.parametrize("exact", [True, False])
def test_successful_source_restores_failed_canonical_write(database, monkeypatch, exact):
    harness = _harness(database)
    orchestrator, adapter, _, _, service, _, _ = harness
    assert orchestrator.job_execute("ingestion_run").status == "success"
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b'tradePrice="100"', b'tradePrice="200"')
    build = service.ledger_snapshot_build_and_persist

    def fail_snapshot(**kwargs):
        raise RuntimeError("failure after canonical commit")

    monkeypatch.setattr(service, "ledger_snapshot_build_and_persist", fail_snapshot)
    assert orchestrator.job_execute("ingestion_run").status == "failed"
    monkeypatch.setattr(service, "ledger_snapshot_build_and_persist", build)
    with database.connect() as connection:
        assert connection.scalar(text("SELECT price FROM event_trade_fill")) == Decimal("200")

    # Another failure and a successful partial import must not clear invalidation.
    fetch = adapter.adapter_fetch_report

    def fail_fetch(**kwargs):
        raise ConnectionError("fetch failed before semantic work")

    monkeypatch.setattr(adapter, "adapter_fetch_report", fail_fetch)
    assert orchestrator.job_execute("ingestion_run").status == "failed"
    monkeypatch.setattr(adapter, "adapter_fetch_report", fetch)
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b'<Trades>', b'<IgnoredTrades>').replace(
        b'</Trades>', b'</IgnoredTrades><Trades />'
    )
    assert orchestrator.job_execute("ingestion_run").status == "success"
    # Retention removes diagnostic payloads, but run status remains durable.
    with database.begin() as connection:
        connection.execute(text("UPDATE ingestion_run SET diagnostics='[]'::jsonb"))
    adapter.payload_bytes = _SEEDED_PAYLOAD if exact else _SEEDED_PAYLOAD + b"\n"
    assert orchestrator.job_execute("ingestion_run").status == "success"
    with database.connect() as connection:
        assert connection.scalar(text("SELECT price FROM event_trade_fill")) == Decimal("100")
        assert connection.scalar(text("SELECT cost_basis FROM pnl_snapshot_daily")) == Decimal("201")


def test_replay_uses_raw_row_owner_after_insert_recovery(database, monkeypatch):
    harness = _harness(database)
    orchestrator, _, raw, _, _, _, _ = harness
    insert = raw.db_raw_record_insert_many

    def fail_insert(requests):
        raise RuntimeError("artifact committed before raw row failure")

    monkeypatch.setattr(raw, "db_raw_record_insert_many", fail_insert)
    assert orchestrator.job_execute("ingestion_run").status == "failed"
    monkeypatch.setattr(raw, "db_raw_record_insert_many", insert)
    assert orchestrator.job_execute("ingestion_run").status == "success"
    with database.connect() as connection:
        period = connection.scalar(text("SELECT period_key FROM raw_artifact"))
        assert connection.scalar(text("SELECT position_qty FROM pnl_snapshot_daily")) == Decimal("2")
    assert _replay(harness, period).status == "success"
    with database.connect() as connection:
        assert connection.scalar(text("SELECT position_qty FROM pnl_snapshot_daily")) == Decimal("2")
        assert connection.scalar(text("SELECT valuation_source FROM pnl_snapshot_daily")) != "broker_position_absent"


def test_daily_synthetic_fx_history_survives_replay_and_legacy_rows(database):
    harness = _harness(database)
    orchestrator, adapter, _, _, _, _, _ = harness
    for day, rate in [("20260820", "1.1"), ("20260821", "1.2")]:
        adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b"20260821", day.encode()).replace(
            b"<ConversionRates />",
            f'<ConversionRates><ConversionRate fromCurrency="EUR" toCurrency="USD" '
            f'reportDate="{day}" rate="{rate}" /></ConversionRates>'.encode(),
        )
        assert orchestrator.job_execute("ingestion_run").status == "success"
    with database.begin() as connection:
        rows = connection.execute(text("SELECT report_date_local, fx_rate FROM event_fx ORDER BY report_date_local")).all()
        assert rows == [(date(2026, 8, 20), Decimal("1.1")), (date(2026, 8, 21), Decimal("1.2"))]
        # Reproduce the pre-fix ordinal event: latest value with old raw provenance.
        connection.execute(text(
            "DELETE FROM event_fx WHERE report_date_local='2026-08-20'"
        ))
        connection.execute(text(
            "UPDATE event_fx SET transaction_id='ConversionRates:ConversionRate:idx=1', "
            "source_raw_record_id=(SELECT raw_record_id FROM raw_record "
            "WHERE section_name='ConversionRates' AND source_payload->>'rate'='1.1')"
        ))
        period = connection.scalar(text("SELECT period_key FROM raw_artifact LIMIT 1"))
    assert _replay(harness, period).status == "success"
    assert _replay(harness, period).status == "success"
    with database.connect() as connection:
        rows = connection.execute(text("SELECT report_date_local, fx_rate FROM event_fx ORDER BY report_date_local")).all()
        assert rows == [(date(2026, 8, 20), Decimal("1.1")), (date(2026, 8, 21), Decimal("1.2"))]


@pytest.mark.parametrize("with_cash_row", [True, False])
def test_cash_dividend_action_requires_review_without_inventing_cashflows(database, with_cash_row):
    harness = _harness(database)
    orchestrator, adapter, _, _, _, _, _ = harness
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(
        b"<CorporateActions />",
        b'<CorporateActions><CorporateAction actionID="CD1" transactionID="CD2" '
        b'conid="900001" symbol="SEED" type="CD" amount="100" withholdingTax="15" '
        b'currency="USD" reportDate="20260821" /></CorporateActions>',
    )
    if with_cash_row:
        adapter.payload_bytes = adapter.payload_bytes.replace(
            b'amount="0" amountInBase="0" withholdingTax="0"',
            b'amount="100" amountInBase="100" withholdingTax="15"',
        )
    assert orchestrator.job_execute("ingestion_run").status == "success"
    with database.connect() as connection:
        assert connection.scalar(text("SELECT requires_manual FROM event_corp_action")) is True
        assert connection.scalar(text("SELECT COUNT(*) FROM corporate_action_manual_case")) == 1
        assert connection.scalar(text("SELECT provisional FROM pnl_snapshot_daily")) is True
        assert connection.scalar(text("SELECT SUM(amount) FROM event_cashflow WHERE instrument_id IS NOT NULL")) == (
            Decimal("100") if with_cash_row else Decimal("0")
        )


def test_failed_history_does_not_disable_other_accounts_duplicate_skip(database, monkeypatch):
    bad = _harness(database, account="FAILED_ACCOUNT")

    def fail_fetch(**kwargs):
        raise ConnectionError("failed account")

    monkeypatch.setattr(bad[1], "adapter_fetch_report", fail_fetch)
    assert bad[0].job_execute("ingestion_run").status == "failed"
    clean = _harness(database, account="CLEAN_ACCOUNT")
    assert clean[0].job_execute("ingestion_run").status == "success"
    assert clean[0].job_execute("ingestion_run").status == "success"
    with database.connect() as connection:
        diagnostics = connection.scalar(text(
            "SELECT diagnostics FROM ingestion_run WHERE account_id='CLEAN_ACCOUNT' "
            "ORDER BY started_at_utc DESC LIMIT 1"
        ))
    assert any(
        event.get("details", {}).get("canonical_skip_reason") == "exact_duplicate_artifact"
        for event in diagnostics
    )
