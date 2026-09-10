"""PostgreSQL regressions for corrected Flex executions and historical replay."""

from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.jobs import ingestion_orchestrator
from test_end_to_end_seeded import _SEEDED_PAYLOAD
from test_ingestion_integrity_regressions import _harness, _replay, database as _database


database = _database


@pytest.mark.parametrize("direct_rate", [False, True], ids=["cash-ratio", "direct-rate"])
def test_trade_correction_updates_execution_fx_inputs(database, direct_rate):
    orchestrator, adapter, *_ = _harness(database)
    original = _SEEDED_PAYLOAD.replace(b'currency="USD"', b'currency="EUR"').replace(
        b'fifoPnlRealized="0" fxRateToBase="1"',
        b'fifoPnlRealized="0" netCash="-201" netCashInBase="-221.10"'
        + (b' fxRateToBase="1.1"' if direct_rate else b''),
    )
    adapter.payload_bytes = original
    assert orchestrator.job_execute("ingestion_run").status == "success"
    with database.connect() as connection:
        origin = connection.execute(text(
            "SELECT ingestion_run_id, source_raw_record_id FROM event_trade_fill"
        )).one()

    adapter.payload_bytes = (
        original.replace(b'ibCommission="1"', b'ibCommission="2"')
        .replace(b'netCash="-201"', b'netCash="-202"')
        .replace(b'netCashInBase="-221.10"', b'netCashInBase="-222.20"')
    )
    if direct_rate:
        adapter.payload_bytes = adapter.payload_bytes.replace(
            b'fxRateToBase="1.1"', b'fxRateToBase="1.2"'
        ).replace(b'netCashInBase="-222.20"', b'netCashInBase="-242.40"')
    assert orchestrator.job_execute("ingestion_run").status == "success"

    with database.connect() as connection:
        trade = connection.execute(text(
            "SELECT ingestion_run_id, source_raw_record_id, commission, net_cash, "
            "net_cash_in_base, fx_rate_to_base FROM event_trade_fill"
        )).one()
        assert (trade.ingestion_run_id, trade.source_raw_record_id) == tuple(origin)
        assert trade.commission == Decimal("2")
        assert trade.net_cash == Decimal("-202")
        assert trade.net_cash_in_base == Decimal("-242.40" if direct_rate else "-222.20")
        assert trade.fx_rate_to_base == (Decimal("1.2") if direct_rate else None)
        assert connection.scalar(text("SELECT cost_basis FROM pnl_snapshot_daily")) == Decimal(
            "242.40" if direct_rate else "222.20"
        )


@pytest.mark.parametrize("exact_retry", [True, False], ids=["exact-report", "changed-artifact"])
@pytest.mark.parametrize("padded_execution_id", [False, True])
def test_historical_replay_preserves_newer_trade_correction(database, monkeypatch, exact_retry, padded_execution_id):
    harness = _harness(database)
    orchestrator, adapter, *_ = harness
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-21")
    assert orchestrator.job_execute("ingestion_run").status == "success"
    corrected = _SEEDED_PAYLOAD.replace(b'tradePrice="100"', b'tradePrice="200"')
    if padded_execution_id:
        corrected = corrected.replace(b'ibExecID="SEED-EXEC-1"', b'ibExecID=" SEED-EXEC-1 "')
    adapter.payload_bytes = corrected
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-22")
    assert orchestrator.job_execute("ingestion_run").status == "success"

    assert _replay(harness, "2026-08-21").status == "success"
    with database.connect() as connection:
        assert connection.scalar(text("SELECT price FROM event_trade_fill")) == Decimal("200")
        assert connection.scalar(text("SELECT cost_basis FROM pnl_snapshot_daily")) == Decimal("401")

    adapter.payload_bytes = corrected if exact_retry else corrected + b"\n"
    assert orchestrator.job_execute("ingestion_run").status == "success"
    with database.connect() as connection:
        assert connection.scalar(text("SELECT price FROM event_trade_fill")) == Decimal("200")


@pytest.mark.parametrize("failed_later_import", [False, True])
def test_replay_uses_latest_successful_event_versions_across_queries(database, monkeypatch, failed_later_import):
    harness = _harness(database)
    orchestrator, adapter, _, _, snapshot_service, *_ = harness
    original = _SEEDED_PAYLOAD.replace(
        b'<ConversionRates />',
        b'<ConversionRates><ConversionRate reportDate="20260821" fromCurrency="EUR" '
        b'toCurrency="USD" rate="1.1" /></ConversionRates>',
    ).replace(
        b'<CorporateActions />',
        b'<CorporateActions><CorporateAction actionID="ACTION1" conid="900001" type="CD" '
        b'reportDate="20260821" currency="USD" description="Original action" /></CorporateActions>',
    )
    adapter.payload_bytes = original
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-21")
    assert orchestrator.job_execute("ingestion_run").status == "success"

    corrected = (
        original.replace(b'tradePrice="100"', b'tradePrice="200"')
        .replace(b'amount="0" amountInBase="0"', b'amount="12" amountInBase="12"')
        .replace(b'rate="1.1"', b'rate="1.2"')
        .replace(b'Original action', b'Corrected action')
    )
    adapter.payload_bytes = corrected
    fetch = adapter.adapter_fetch_report
    monkeypatch.setattr(adapter, "adapter_fetch_report", lambda query_id: fetch("seeded-query"))
    orchestrator._config = replace(orchestrator._config, flex_query_id="other-query")
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-22")
    assert orchestrator.job_execute("ingestion_run").status == "success"

    if failed_later_import:
        adapter.payload_bytes = (
            corrected.replace(b'tradePrice="200"', b'tradePrice="300"')
            .replace(b'amount="12" amountInBase="12"', b'amount="99" amountInBase="99"')
            .replace(b'rate="1.2"', b'rate="1.3"')
            .replace(b'Corrected action', b'Failed action')
        )
        build = snapshot_service.ledger_snapshot_build_and_persist

        def fail_snapshot(**kwargs):
            raise RuntimeError("failure after canonical write")

        monkeypatch.setattr(snapshot_service, "ledger_snapshot_build_and_persist", fail_snapshot)
        assert orchestrator.job_execute("ingestion_run").status == "failed"
        monkeypatch.setattr(snapshot_service, "ledger_snapshot_build_and_persist", build)

    assert _replay(harness, "2026-08-21").status == "success"
    with database.connect() as connection:
        assert connection.scalar(text("SELECT price FROM event_trade_fill")) == Decimal("200")
        assert connection.scalar(text("SELECT amount FROM event_cashflow WHERE transaction_id='9002'")) == Decimal("12")
        assert connection.scalar(text("SELECT fx_rate FROM event_fx")) == Decimal("1.2")
        assert connection.scalar(text("SELECT description FROM event_corp_action")) == "Corrected action"


@pytest.mark.parametrize("rebuild", [False, True], ids=["existing-events", "missing-events"])
@pytest.mark.parametrize("description_present", [False, True])
def test_replay_retains_event_origins_and_current_description(database, monkeypatch, rebuild, description_present):
    harness = _harness(database)
    orchestrator, adapter, *_ = harness
    original = _SEEDED_PAYLOAD.replace(
        b'ibExecID="SEED-EXEC-1"', b'ibExecID="SEED-EXEC-1" description="Original"',
    ).replace(
        b'<ConversionRates />',
        b'<ConversionRates><ConversionRate reportDate="20260821" fromCurrency="EUR" '
        b'toCurrency="USD" rate="1.1" /></ConversionRates>',
    )
    adapter.payload_bytes = original
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-21")
    assert orchestrator.job_execute("ingestion_run").status == "success"
    with database.connect() as connection:
        origins = {
            table: connection.execute(text(f"SELECT ingestion_run_id, source_raw_record_id FROM {table}")).one()
            for table in ("event_trade_fill", "event_fx")
        }

    corrected = original.replace(b'Original', b'Corrected').replace(
        b'tradePrice="100"', b'tradePrice="200"',
    ).replace(b'rate="1.1"', b'rate="1.2"')
    adapter.payload_bytes = corrected
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-22")
    assert orchestrator.job_execute("ingestion_run").status == "success"
    if not description_present:
        adapter.payload_bytes = corrected.replace(b' description="Corrected"', b'')
        assert orchestrator.job_execute("ingestion_run").status == "success"

    with database.begin() as connection:
        updated_at = connection.scalar(text("SELECT updated_at_utc FROM event_trade_fill"))
        if rebuild:
            connection.execute(text("DELETE FROM position_lot"))
            connection.execute(text("DELETE FROM pnl_snapshot_daily"))
            connection.execute(text("DELETE FROM event_trade_fill"))
            connection.execute(text("DELETE FROM event_fx"))

    assert _replay(harness, "2026-08-21").status == "success"
    with database.connect() as connection:
        for table, origin in origins.items():
            assert connection.execute(text(
                f"SELECT ingestion_run_id, source_raw_record_id FROM {table}"
            )).one() == origin
        trade = connection.execute(text("SELECT price, description, updated_at_utc FROM event_trade_fill")).one()
        assert trade.price == Decimal("200")
        assert trade.description == (None if rebuild and not description_present else "Corrected")
        assert connection.scalar(text("SELECT fx_rate FROM event_fx")) == Decimal("1.2")
        if not rebuild:
            assert trade.updated_at_utc == updated_at
