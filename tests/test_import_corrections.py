"""PostgreSQL regressions for Flex trade refreshes and historical replay."""

from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.jobs import ingestion_orchestrator
from test_end_to_end_seeded import _SEEDED_PAYLOAD
from test_ingestion_integrity_regressions import _harness, _replay, database as _database


database = _database


@pytest.mark.parametrize("direct_rate", [False, True], ids=["cash-ratio", "direct-rate"])
def test_trade_derived_fx_values_can_refresh(database, direct_rate):
    orchestrator, adapter, *_ = _harness(database)
    original = _SEEDED_PAYLOAD.replace(b'currency="USD"', b'currency="EUR"').replace(
        b'<AccountInformation accountId="U_TEST" currency="EUR"', b'<AccountInformation accountId="U_TEST" currency="USD"',
    ).replace(
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
        original.replace(b'netCashInBase="-221.10"', b'netCashInBase="-231.15"')
    )
    if direct_rate:
        adapter.payload_bytes = adapter.payload_bytes.replace(
            b'fxRateToBase="1.1"', b'fxRateToBase="1.2"'
        ).replace(b'netCashInBase="-231.15"', b'netCashInBase="-241.20"')
    assert orchestrator.job_execute("ingestion_run").status == "success"

    with database.connect() as connection:
        trade = connection.execute(text(
            "SELECT ingestion_run_id, source_raw_record_id, commission, net_cash, "
            "net_cash_in_base, fx_rate_to_base FROM event_trade_fill"
        )).one()
        assert (trade.ingestion_run_id, trade.source_raw_record_id) == tuple(origin)
        assert trade.commission == Decimal("1")
        assert trade.net_cash == Decimal("-201")
        assert trade.net_cash_in_base == Decimal("-241.20" if direct_rate else "-231.15")
        assert trade.fx_rate_to_base == (Decimal("1.2") if direct_rate else None)
        assert connection.scalar(text("SELECT cost_basis FROM pnl_snapshot_daily")) == Decimal(
            "241.20" if direct_rate else "231.15"
        )


@pytest.mark.parametrize("exact_retry", [True, False], ids=["exact-report", "changed-artifact"])
@pytest.mark.parametrize("padded_execution_id", [False, True])
def test_historical_replay_preserves_newer_trade_refresh(database, monkeypatch, exact_retry, padded_execution_id):
    harness = _harness(database)
    orchestrator, adapter, *_ = harness
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-21")
    assert orchestrator.job_execute("ingestion_run").status == "success"
    corrected = _SEEDED_PAYLOAD.replace(b'tradePrice="100"', b'tradePrice="100" cost="400"')
    if padded_execution_id:
        corrected = corrected.replace(b'ibExecID="SEED-EXEC-1"', b'ibExecID=" SEED-EXEC-1 "')
    adapter.payload_bytes = corrected
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-22")
    assert orchestrator.job_execute("ingestion_run").status == "success"

    assert _replay(harness, "2026-08-21").status == "success"
    with database.connect() as connection:
        assert connection.scalar(text("SELECT cost FROM event_trade_fill")) == Decimal("400")
        assert connection.scalar(text("SELECT cost_basis FROM pnl_snapshot_daily")) == Decimal("201")

    adapter.payload_bytes = corrected if exact_retry else corrected + b"\n"
    assert orchestrator.job_execute("ingestion_run").status == "success"
    with database.connect() as connection:
        assert connection.scalar(text("SELECT cost FROM event_trade_fill")) == Decimal("400")


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
        original.replace(b'tradePrice="100"', b'tradePrice="100" cost="400"')
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
        with database.connect() as connection:
            published_state = {
                table: connection.execute(text(f"SELECT * FROM {table}")).all()
                for table in (
                    "instrument", "event_trade_fill", "event_cashflow", "event_fx",
                    "event_corp_action", "corporate_action_manual_case", "position_lot", "pnl_snapshot_daily",
                )
            }
        adapter.payload_bytes = (
            corrected.replace(b'cost="400"', b'cost="600"')
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
        with database.connect() as connection:
            for table, expected_rows in published_state.items():
                assert connection.execute(text(f"SELECT * FROM {table}")).all() == expected_rows
            assert "failure after canonical write" in str(connection.scalar(text(
                "SELECT diagnostics FROM ingestion_run WHERE status='failed'"
            )))
            assert connection.scalar(text(
                "SELECT count(*) FROM raw_artifact WHERE completed_ingestion_run_id IS NOT NULL"
            )) == 2

    assert _replay(harness, "2026-08-21").status == "success"
    with database.connect() as connection:
        assert connection.scalar(text("SELECT cost FROM event_trade_fill")) == Decimal("400")
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
        b'tradePrice="100"', b'tradePrice="100" cost="400"',
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
        trade = connection.execute(text("SELECT cost, description, updated_at_utc FROM event_trade_fill")).one()
        assert trade.cost == Decimal("400")
        assert trade.description == (None if rebuild and not description_present else "Corrected")
        assert connection.scalar(text("SELECT fx_rate FROM event_fx")) == Decimal("1.2")
        if not rebuild:
            assert trade.updated_at_utc == updated_at


@pytest.mark.parametrize(("attribute", "value"), [
    ("quantity", "3"), ("buySell", "SELL"), ("dateTime", "20260821;140000"),
    ("currency", "EUR"), ("fees", "7"), ("conid", "900002"),
    ("transactionID", "conflicting-transaction"), ("ibExecID", "conflicting-execution"),
    ("tradePrice", "200"), ("ibCommission", "2"), ("netCash", "-401"),
])
def test_rejected_trade_change_preserves_publication_and_missing_trade_rebuild(
    database, monkeypatch, attribute, value,
):
    import xml.etree.ElementTree as element_tree

    harness = _harness(database)
    orchestrator, adapter, *_ = harness
    original = _SEEDED_PAYLOAD.replace(b'fees="0" fifoPnlRealized="0"', b'fees="0" netCash="-201" fifoPnlRealized="0"')
    adapter.payload_bytes = original
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-21")
    assert orchestrator.job_execute("ingestion_run").status == "success"

    trade_query = text(
        "SELECT instrument_id, ingestion_run_id, source_raw_record_id, ib_exec_id, transaction_id, "
        "trade_timestamp_utc, report_date_local, side, quantity, price, cost, commission, fees, "
        "realized_pnl, net_cash, net_cash_in_base, fx_rate_to_base, currency, functional_currency, "
        "description FROM event_trade_fill"
    )
    lot_query = text("SELECT open_quantity, open_price, cost_basis_open, remaining_quantity FROM position_lot")
    with database.connect() as connection:
        expected_trade = connection.execute(trade_query).one()
        expected_lot = connection.execute(lot_query).one()
        published_state = {
            table: connection.execute(text(f"SELECT * FROM {table}")).all()
            for table in ("instrument", "event_trade_fill", "event_cashflow", "position_lot", "pnl_snapshot_daily")
        }

    root = element_tree.fromstring(original)
    trade = root.find(".//Trade")
    assert trade is not None
    trade.set(attribute, value)
    trade.set("description", "Rejected trade change")
    adapter.payload_bytes = element_tree.tostring(root)
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-22")
    assert orchestrator.job_execute("ingestion_run").status == "failed"
    with database.connect() as connection:
        for table, expected_rows in published_state.items():
            assert connection.execute(text(f"SELECT * FROM {table}")).all() == expected_rows
        assert connection.scalar(text(
            "SELECT count(*) FROM raw_artifact WHERE completed_ingestion_run_id IS NOT NULL"
        )) == 1
        assert "TRADE_CONSISTENCY_CONFLICT" in str(connection.scalar(text(
            "SELECT diagnostics FROM ingestion_run WHERE status='failed'"
        )))

    assert _replay(harness, "2026-08-21").status == "success"
    with database.begin() as connection:
        assert connection.execute(trade_query).one() == expected_trade
        assert connection.execute(lot_query).one() == expected_lot
        connection.execute(text("DELETE FROM position_lot"))
        connection.execute(text("DELETE FROM pnl_snapshot_daily"))
        connection.execute(text("DELETE FROM event_trade_fill"))

    assert _replay(harness, "2026-08-21").status == "success"
    with database.connect() as connection:
        assert connection.execute(trade_query).one() == expected_trade
        assert connection.execute(lot_query).one() == expected_lot
