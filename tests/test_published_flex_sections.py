"""PostgreSQL behavior checks for published Flex section source selection."""

import json
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, text

from app.db.portfolio import SQLAlchemyPortfolioService
from app.db.published_flex_sections import (
    db_published_flex_sections_history,
    db_published_flex_sections_latest,
)
from test_ingestion_integrity_regressions import database as _database


database = _database
DAY = date(2026, 10, 3)
CREATED = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def _source(
    connection: Connection, sections: dict[str, list[dict]], *,
    account: str = "SECTIONS", report_date: date | None = DAY, created_at: datetime = CREATED,
    owner_status: str = "success", completion_status: str | None = None, artifact_id: UUID | None = None,
) -> UUID:
    """Insert immutable section versions, including present-empty markers and recovery ownership."""
    artifact_id = artifact_id or uuid4()
    params = {"account": account, "date": report_date, "created": created_at,
              "artifact": artifact_id, "sha": str(artifact_id)}
    statuses = [owner_status] if completion_status is None else [owner_status, completion_status]
    runs = [connection.execute(text(
        "INSERT INTO ingestion_run(account_id,run_type,status,period_key,flex_query_id,started_at_utc) "
        "VALUES (:account,'manual',:status,'source-test','query',:created) RETURNING ingestion_run_id"
    ), {**params, "status": status}).scalar_one() for status in statuses]
    connection.execute(text(
        "INSERT INTO raw_artifact(raw_artifact_id,ingestion_run_id,account_id,period_key,flex_query_id,"
        "payload_sha256,report_date_local,source_payload,created_at_utc,completed_ingestion_run_id) "
        "VALUES (:artifact,:run,:account,'source-test','query',:sha,:date,:payload,:created,:completion)"
    ), {**params, "run": runs[0], "completion": runs[1] if completion_status is not None else None,
        "payload": b"<FlexStatement/>"})
    for section, payloads in sections.items():
        for index, payload in enumerate(payloads or [{}]):
            reference = f"{section}:{'row' if payloads else 'section'}:{index + 1}"
            connection.execute(text(
                "INSERT INTO raw_record(raw_artifact_id,ingestion_run_id,account_id,period_key,flex_query_id,"
                "payload_sha256,report_date_local,section_name,source_row_ref,source_payload,created_at_utc) "
                "VALUES (:artifact,:run,:account,'source-test','query',:sha,:date,:section,:reference,"
                "CAST(:payload AS jsonb),:created)"
            ), {**params, "run": runs[0], "section": section, "reference": reference, "payload": json.dumps(payload)})
    return artifact_id


@pytest.mark.parametrize("owner_status,completion_status,eligible", [
    ("success", None, True), ("failed", None, False), ("started", None, False),
    ("failed", "success", True), ("success", "failed", False),
    ("success", "started", False), ("success", "success", True),
])
def test_publication_status_controls_latest_and_history(database, owner_status, completion_status, eligible):
    with database.begin() as connection:
        previous = _source(connection, {"CashReport": []}, report_date=DAY - timedelta(days=1))
        current = _source(connection, {"CashReport": []}, owner_status=owner_status,
                          completion_status=completion_status)
        latest = connection.execute(db_published_flex_sections_latest("SECTIONS", ("CashReport",))).mappings().one()
        assert latest["raw_artifact_id"] == (current if eligible else previous)
        history = connection.execute(db_published_flex_sections_history("SECTIONS", ("CashReport",))).mappings()
        assert {row["raw_artifact_id"] for row in history} == ({previous, current} if eligible else {previous})


@pytest.mark.parametrize("winner_date,winner_created,winner_id", [
    (DAY + timedelta(days=1), CREATED - timedelta(hours=1), UUID(int=1)),
    (DAY, CREATED + timedelta(hours=1), UUID(int=1)),
    (DAY, CREATED, UUID(int=3)),
], ids=["statement-date", "creation-time", "artifact-identity"])
def test_latest_uses_statement_date_creation_and_identity(database, winner_date, winner_created, winner_id):
    with database.begin() as connection:
        _source(connection, {"CashReport": []}, artifact_id=UUID(int=2))
        _source(connection, {"CashReport": [{"currency": "USD"}, {"currency": "EUR"}]},
                report_date=winner_date, created_at=winner_created, artifact_id=winner_id)
        row = connection.execute(db_published_flex_sections_latest("SECTIONS", ("CashReport",))).mappings().one()
        assert dict(row) == {"section_name": "CashReport", "raw_artifact_id": winner_id,
                             "report_date_local": winner_date, "created_at_utc": winner_created}


def test_empty_section_replaces_prior_source_while_omitted_section_retains_it(database):
    with database.begin() as connection:
        original = _source(connection, {"CashReport": [{"currency": "USD"}],
                                        "OpenPositions": [{"conid": "1"}]})
        emptied = _source(connection, {"OpenPositions": []}, created_at=CREATED + timedelta(hours=1))
        sources = db_published_flex_sections_latest(
            "SECTIONS", ("CashReport", "OpenPositions"),
        ).cte("published_sections")
        rows = connection.execute(text(
            "SELECT s.section_name,s.raw_artifact_id,r.source_payload FROM published_sections s "
            "JOIN raw_record r ON r.raw_artifact_id=s.raw_artifact_id AND r.section_name=s.section_name "
            "ORDER BY s.section_name"
        ).columns().add_cte(sources)).mappings().all()
        assert [dict(row) for row in rows] == [
            {"section_name": "CashReport", "raw_artifact_id": original, "source_payload": {"currency": "USD"}},
            {"section_name": "OpenPositions", "raw_artifact_id": emptied, "source_payload": {}},
        ]


@pytest.mark.parametrize("include_undated", [False, True])
def test_history_retains_versions_and_section_identities_with_explicit_date_policy(database, include_undated):
    with database.begin() as connection:
        original = _source(connection, {"EquitySummaryInBase": [{"total": "1"}, {"total": "2"}],
                                        "OpenDividendAccruals": []})
        corrected = _source(connection, {"EquitySummaryInBase": [{"total": "3"}]},
                            created_at=CREATED + timedelta(hours=1))
        undated = _source(connection, {"EquitySummaryInBase": []}, report_date=None)
        _source(connection, {"EquitySummaryInBase": []}, owner_status="failed")
        _source(connection, {"EquitySummaryInBase": []}, account="OTHER", report_date=DAY + timedelta(days=1))
        sections = ("EquitySummaryInBase", "OpenDividendAccruals")
        history = connection.execute(db_published_flex_sections_history(
            "SECTIONS", sections, include_undated=include_undated,
        )).mappings().all()
        expected = {("EquitySummaryInBase", original), ("OpenDividendAccruals", original),
                    ("EquitySummaryInBase", corrected)}
        if include_undated:
            expected.add(("EquitySummaryInBase", undated))
        assert {(row["section_name"], row["raw_artifact_id"]) for row in history} == expected
        assert len(history) == len(expected)
        latest = connection.execute(db_published_flex_sections_latest("SECTIONS", sections)).mappings()
        assert {row["section_name"]: row["raw_artifact_id"] for row in latest} == {
            "EquitySummaryInBase": corrected, "OpenDividendAccruals": original,
        }


@pytest.mark.parametrize("sections", [(), ("MissingSection",)])
def test_unrequested_sections_do_not_produce_sources(database, sections):
    with database.begin() as connection:
        _source(connection, {"CashReport": []})
        assert connection.execute(db_published_flex_sections_latest("SECTIONS", sections)).all() == []
        assert connection.execute(db_published_flex_sections_history("SECTIONS", sections)).all() == []


def test_portfolio_tax_history_includes_undated_sources_and_keeps_event_deduplication(database):
    def tax(identity, amount):
        return {"tradeID": identity, "taxAmount": amount, "currency": "USD", "taxDescription": "Transaction tax"}

    with database.begin() as connection:
        _source(connection, {"TransactionTaxes": [tax("original", "-1")]})
        _source(connection, {"TransactionTaxes": [tax("original", "-2")]},
                created_at=CREATED + timedelta(hours=1))
        _source(connection, {"TransactionTaxes": [tax("undated", "-4")]}, report_date=None)
        _source(connection, {"TransactionTaxes": [tax("original", "-99")]}, owner_status="failed",
                created_at=CREATED + timedelta(hours=2))
    summary = SQLAlchemyPortfolioService(database).db_report_portfolio_summary("SECTIONS")
    assert [(row.category, row.net_cost_usd) for row in summary.cost_summary] == [("Transaction tax", "6")]
