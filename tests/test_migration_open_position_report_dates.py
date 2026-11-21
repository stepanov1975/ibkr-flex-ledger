"""Legacy snapshots with conflicting OpenPositions dates remain auditable."""

from decimal import Decimal
import xml.etree.ElementTree as ET

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from test_end_to_end_seeded import _SEEDED_PAYLOAD
from test_ingestion_integrity_regressions import _harness, database as _database


database = _database


def test_migration_invalidates_legacy_valuation_without_rewriting_amounts(database):
    config = Config("alembic.ini")
    command.downgrade(config, "20261117_19")
    root = ET.fromstring(_SEEDED_PAYLOAD)
    position = root.find(".//OpenPosition")
    position.set("multiplier", "1")
    position.set("reportDate", "2026/08/21")
    for account in ("OLD", "CURRENT"):
        orchestrator, adapter, *_ = _harness(database, account=account)
        adapter.payload_bytes = ET.tostring(root)
        assert orchestrator.job_execute("ingestion_run").status == "success"
    with database.begin() as connection:
        connection.execute(text(
            "UPDATE raw_record SET source_payload = jsonb_set(source_payload, '{reportDate}', to_jsonb(CAST(:day AS text))) "
            "WHERE account_id='OLD' AND section_name='OpenPositions'"
        ), {"day": "20260820"})

    command.upgrade(config, "20261121_20")
    with database.connect() as connection:
        rows = [tuple(row) for row in connection.execute(text(
            "SELECT account_id, unrealized_pnl, cost_basis, calculation_provisional, provisional, valuation_source "
            "FROM pnl_snapshot_daily ORDER BY account_id"
        ))]
    assert rows == [
        ("CURRENT", Decimal("19"), Decimal("201"), False, False, "openpositions_mark_price"),
        ("OLD", Decimal("19"), Decimal("201"), True, True, "openpositions_summary_unavailable"),
    ]
