"""Replay sources follow successful application order through artifact recovery."""

from decimal import Decimal

from sqlalchemy import text

from app.jobs import ingestion_orchestrator
from app.jobs.replay_sources import job_replay_event_sources
from test_end_to_end_seeded import _SEEDED_PAYLOAD
from test_ingestion_integrity_regressions import _harness, _replay, database as _database


database = _database


def _fail_before_raw_rows(*args, **kwargs):
    raise RuntimeError("artifact persisted before raw-row failure")


def test_replay_sources_follow_recovery_application_order(database, monkeypatch):
    harness = _harness(database)
    orchestrator, adapter, raw, canonical, *_ = harness
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-21")
    with monkeypatch.context() as failure:
        failure.setattr(raw, "db_raw_record_insert_many", _fail_before_raw_rows)
        assert orchestrator.job_execute("ingestion_run").status == "failed"

    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b'quantity="2"', b'quantity="3"').replace(
        b'tradePrice="100"', b'tradePrice="200"',
    )
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-22")
    assert orchestrator.job_execute("ingestion_run").status == "success"
    with database.connect() as connection:
        origin = connection.scalar(text("SELECT source_raw_record_id FROM event_trade_fill"))

    adapter.payload_bytes = _SEEDED_PAYLOAD
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-21")
    assert orchestrator.job_execute("ingestion_run").status == "success"
    rows = canonical.db_raw_record_list_successful_events_for_account("INTEGRITY")
    trades = [row for row in rows if row.section_name == "Trades"]
    assert [row.source_payload["tradePrice"] for row in trades] == ["200", "100"]
    sources = job_replay_event_sources("INTEGRITY", "USD", rows)
    key = ("trade", "SEED-EXEC-1")
    assert sources.first[key].raw_record_id == origin
    assert sources.latest[key].raw_record_id == trades[-1].raw_record_id

    assert _replay(harness, "2026-08-22").status == "success"
    with database.connect() as connection:
        assert connection.execute(text("SELECT quantity, price FROM event_trade_fill")).one() == (
            Decimal("3"), Decimal("100"),
        )


def test_reapplying_successful_artifact_retains_first_application_origin(database, monkeypatch):
    harness = _harness(database)
    orchestrator, adapter, _, canonical, *_ = harness
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-21")
    assert orchestrator.job_execute("ingestion_run").status == "success"
    with database.connect() as connection:
        origin = connection.scalar(text("SELECT source_raw_record_id FROM event_trade_fill"))

    adapter.payload_bytes = _SEEDED_PAYLOAD.replace(b'tradePrice="100"', b'tradePrice="200"')
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-22")
    assert orchestrator.job_execute("ingestion_run").status == "success"
    with monkeypatch.context() as failure:
        failure.setattr(adapter, "adapter_fetch_report", _fail_before_raw_rows)
        assert orchestrator.job_execute("ingestion_run").status == "failed"
    adapter.payload_bytes = _SEEDED_PAYLOAD
    monkeypatch.setattr(ingestion_orchestrator, "snapshot_resolve_report_date_local", lambda _: "2026-08-21")
    assert orchestrator.job_execute("ingestion_run").status == "success"

    rows = canonical.db_raw_record_list_successful_events_for_account("INTEGRITY")
    assert [row.source_payload["tradePrice"] for row in rows if row.section_name == "Trades"] == [
        "100", "200", "100",
    ]
    sources = job_replay_event_sources("INTEGRITY", "USD", rows)
    key = ("trade", "SEED-EXEC-1")
    assert sources.first[key].raw_record_id == origin
    assert sources.latest[key].raw_record_id == origin
