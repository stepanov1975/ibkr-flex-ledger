"""Real PostgreSQL regressions for failed ingestion, FX history and recovery replay."""

from datetime import date
from decimal import Decimal
from uuid import uuid4
import xml.etree.ElementTree as ET

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
from app.db.published_flex_sections import db_published_flex_sections_by_date
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
def test_successful_source_after_failed_import_remains_consistent(database, monkeypatch, exact):
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
        assert connection.scalar(text("SELECT price FROM event_trade_fill")) == Decimal("100")

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


def test_historical_position_correction_uses_snapshot_date(database):
    orchestrator, adapter, *_ = _harness(database)
    statement = ET.fromstring(_SEEDED_PAYLOAD)
    header = statement.find('.//FlexStatement')
    position = header.find('./OpenPositions/OpenPosition')
    position.attrib.pop('reportDate')
    position.set('multiplier', '1')
    adapter.payload_bytes = ET.tostring(statement)
    assert orchestrator.job_execute('ingestion_run').status == 'success'

    position.set('markPrice', '120')
    position.set('positionValue', '240')
    for day in ('20260822', '20260821'):
        header.set('reportDate', day)
        adapter.payload_bytes = ET.tostring(statement)
        assert orchestrator.job_execute('ingestion_run').status == 'success'

    with database.connect() as connection:
        assert connection.execute(text(
            'SELECT report_date_local, unrealized_pnl FROM pnl_snapshot_daily ORDER BY report_date_local'
        )).all() == [(date(2026, 8, 21), Decimal('39')), (date(2026, 8, 22), Decimal('39'))]


def test_position_returning_after_empty_section_rebuilds_snapshot(database):
    orchestrator, adapter, *_ = _harness(database)
    initial = _SEEDED_PAYLOAD.replace(b'markPrice="110"', b'markPrice="110" multiplier="1"')
    emptied = ET.fromstring(initial)
    emptied.find('.//OpenPositions').clear()
    for payload, quantity in ((initial, 2), (ET.tostring(emptied), 0), (initial + b'\n', 2)):
        adapter.payload_bytes = payload
        assert orchestrator.job_execute('ingestion_run').status == 'success'
        with database.connect() as connection:
            assert connection.scalar(text('SELECT position_qty FROM pnl_snapshot_daily')) == quantity


def test_cash_correction_after_recovery_matches_replay(database, monkeypatch):
    harness = _harness(database)
    orchestrator, adapter, _, _, service, *_ = harness
    recovered = _SEEDED_PAYLOAD.replace(b'amount="100" amountInBase="100"', b'amount="10" amountInBase="10"')
    adapter.payload_bytes = recovered

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after raw rows are stored')

    with monkeypatch.context() as failure:
        failure.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
        assert orchestrator.job_execute('ingestion_run').status == 'failed'

    corrected = recovered.replace(b'amount="10" amountInBase="10"', b'amount="20" amountInBase="20"')
    amount_query = text("SELECT amount FROM event_cashflow WHERE transaction_id='9003'")
    for payload, amount in ((corrected, 20), (recovered, 10), (corrected + b'\n', 20)):
        adapter.payload_bytes = payload
        assert orchestrator.job_execute('ingestion_run').status == 'success'
        with database.connect() as connection:
            assert connection.scalar(amount_query) == amount
            period = connection.scalar(text('SELECT period_key FROM raw_artifact LIMIT 1'))
    assert _replay(harness, period).status == 'success'
    with database.connect() as connection:
        assert connection.scalar(amount_query) == 20


@pytest.mark.parametrize('changed_query', [False, True], ids=['colliding-raw-reference', 'alternate-query'])
def test_live_cash_correction_with_ambiguous_predecessor_matches_replay(database, monkeypatch, changed_query):
    harness = _harness(database)
    ingestion, adapter, raw, canonical, service, _, runs = harness
    corrected_ingestion = ingestion
    original = _SEEDED_PAYLOAD.replace(b'amount="100" amountInBase="100"', b'amount="10" amountInBase="10"')
    if changed_query:
        fetch = adapter.adapter_fetch_report
        monkeypatch.setattr(adapter, 'adapter_fetch_report', lambda query_id: fetch('seeded-query'))
        corrected_ingestion = IngestionJobOrchestrator(
            ingestion_repository=runs,
            raw_persistence_repository=raw,
            canonical_repository=canonical,
            snapshot_service=service,
            flex_adapter=adapter,
            config=IngestionOrchestratorConfig(account_id='INTEGRITY', flex_query_id='alternate-query'),
        )
    else:
        original = original.replace(
            b'</CashTransactions>',
            b'<CashTransaction transactionID="9003" type="Other Fees" amount="1" '
            b'currency="USD" reportDate="20260820" /></CashTransactions>',
        )

    corrected = original.replace(b'amount="10" amountInBase="10"', b'amount="20" amountInBase="20"')
    query = text("SELECT amount FROM event_cashflow WHERE transaction_id='9003' AND cash_action='Deposits/Withdrawals'")
    for orchestrator, payload, amount in (
        (ingestion, original, 10), (corrected_ingestion, corrected, 20), (ingestion, original + b'\n', 10),
    ):
        adapter.payload_bytes = payload
        assert orchestrator.job_execute('ingestion_run').status == 'success'
        with database.connect() as connection:
            assert connection.scalar(query) == amount
            period = connection.scalar(text('SELECT period_key FROM raw_artifact LIMIT 1'))
    assert _replay(harness, period).status == 'success'
    with database.connect() as connection:
        assert connection.scalar(query) == 10


@pytest.mark.parametrize('recovering_newest_artifact', [False, True])
def test_recovery_and_replay_use_newest_dated_position_section(database, monkeypatch, recovering_newest_artifact):
    harness = _harness(database)
    orchestrator, adapter, _, _, service, *_ = harness
    recovered = _SEEDED_PAYLOAD.replace(b'markPrice="110"', b'markPrice="110" multiplier="1"')
    corrected = recovered.replace(b'markPrice="110"', b'markPrice="120"').replace(
        b'positionValue="220"', b'positionValue="240"',
    )

    def ingest(payload):
        adapter.payload_bytes = payload
        return orchestrator.job_execute('ingestion_run').status

    def fail_snapshot(**kwargs):
        raise RuntimeError('failure after raw rows are stored')

    if recovering_newest_artifact:
        assert ingest(corrected) == 'success'
    with monkeypatch.context() as failure:
        failure.setattr(service, 'ledger_snapshot_build_and_persist', fail_snapshot)
        assert ingest(recovered) == 'failed'
        if not recovering_newest_artifact:
            assert ingest(corrected) == 'failed'
    if not recovering_newest_artifact:
        assert ingest(corrected) == 'success'
    assert ingest(recovered) == 'success'

    published = db_published_flex_sections_by_date('INTEGRITY', ('OpenPositions',)).cte('published')
    expected = Decimal('19' if recovering_newest_artifact else '39')
    valuation_query = text(
        "SELECT snapshot.unrealized_pnl, snapshot.ingestion_run_id=position.ingestion_run_id AS uses_published "
        "FROM pnl_snapshot_daily snapshot JOIN published ON snapshot.report_date_local=published.report_date_local "
        "JOIN raw_record position ON position.raw_artifact_id=published.raw_artifact_id "
        "AND position.section_name=published.section_name"
    ).columns().add_cte(published)
    with database.connect() as connection:
        assert connection.execute(valuation_query).one() == (expected, True)
        period = connection.scalar(text('SELECT period_key FROM raw_artifact LIMIT 1'))
    assert _replay(harness, period).status == 'success'
    with database.connect() as connection:
        assert connection.execute(valuation_query).one() == (expected, True)


@pytest.mark.parametrize(
    'date_attribute', [b'', b' reportDate="-"', b' reportDate="--"', b' reportDate="N/A"', b' reportDate="&#9;N/A&#160;"'],
    ids=['omitted', 'dash', 'double-dash', 'n-a', 'padded-n-a'],
)
def test_equal_conversion_rates_with_inherited_dates_remain_separate_events(database, date_attribute):
    orchestrator, adapter, *_ = _harness(database)
    initial = _SEEDED_PAYLOAD.replace(b'reportDate="20260821" position="2"', b'position="2"').replace(
        b'<ConversionRates />',
        b'<ConversionRates><ConversionRate fromCurrency="EUR" toCurrency="USD" rate="1.1"'
        + date_attribute + b' /></ConversionRates>',
    )
    for report_date in (b'20260821', b'20260822'):
        adapter.payload_bytes = initial.replace(
            b'<FlexStatement reportDate="20260821">', b'<FlexStatement reportDate="' + report_date + b'">',
        )
        assert orchestrator.job_execute('ingestion_run').status == 'success'

    with database.connect() as connection:
        assert connection.execute(text('SELECT report_date_local, fx_rate FROM event_fx ORDER BY report_date_local')).all() == [
            (date(2026, 8, 21), Decimal('1.1')), (date(2026, 8, 22), Decimal('1.1')),
        ]


def test_daily_synthetic_fx_history_survives_replay_and_legacy_rows(database):
    harness = _harness(database)
    orchestrator, adapter, _, _, _, _, _ = harness
    for day, rate in [("20260820", "1.1"), ("20260821", "1.2")]:
        adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b'reportDate="20260821"', f'reportDate="{day}"'.encode()).replace(
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
