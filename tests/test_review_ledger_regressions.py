"""PostgreSQL regressions for commission currency and incremental FX scope."""

from decimal import Decimal

from sqlalchemy import text

from test_end_to_end_seeded import _SEEDED_PAYLOAD
from test_ingestion_integrity_regressions import _harness, database as database


def test_ledger_reads_commission_currency_and_scopes_its_fx_updates(database) -> None:  # noqa: F811
    orchestrator, adapter, _, _, _, snapshots, _ = _harness(database)
    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b'currency="USD"', b'currency="EUR"').replace(
        b'fxRateToBase="1"', b'fxRateToBase="1.2"'
    ).replace(b'ibCommission="1"', b'ibCommission="-1" ibCommissionCurrency=" gbp "').replace(
        b'<ConversionRates />',
        b'<ConversionRates><ConversionRate reportDate="20260821" fromCurrency="GBP" '
        b'toCurrency="USD" rate="1.5" /></ConversionRates>',
    )
    assert orchestrator.job_execute("ingestion_run").status == "success"
    trade = snapshots.db_ledger_trade_fill_list_for_account("INTEGRITY")[0]
    assert trade.commission_currency == "GBP"
    assert snapshots.db_ledger_instrument_ids_for_scope("INTEGRITY", (), ("GBP",)) == [str(trade.instrument_id)]
    with database.connect() as connection:
        row = connection.execute(text("SELECT fees, cost_basis FROM pnl_snapshot_daily")).mappings().one()
    assert row["fees"] == Decimal("1.5")
    assert row["cost_basis"] == Decimal("241.5")

    # A pure conversion-rate correction must invalidate the security snapshot too.
    adapter.payload_bytes = adapter.payload_bytes.replace(b'rate="1.5"', b'rate="1.6"')
    assert orchestrator.job_execute("ingestion_run").status == "success"
    with database.connect() as connection:
        row = connection.execute(text("SELECT fees, cost_basis FROM pnl_snapshot_daily")).mappings().one()
    assert row["fees"] == Decimal("1.6")
    assert row["cost_basis"] == Decimal("241.6")
