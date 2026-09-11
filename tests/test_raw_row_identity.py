"""Regressions for preserving every raw row when broker identifiers repeat."""

from collections import Counter
from datetime import date
from hashlib import sha256
from xml.sax.saxutils import quoteattr

import pytest
from sqlalchemy import text

from app.db import SQLAlchemyIngestionRunService, SQLAlchemyRawPersistenceService
from app.db.interfaces import RawArtifactPersistRequest, RawArtifactReference, RawRecordPersistRequest
from app.jobs.raw_extraction import job_raw_extract_payload_rows
from test_ingestion_integrity_regressions import database as _database


database = _database


def _payload(*statements: str) -> bytes:
    return (
        f'<FlexQueryResponse><FlexStatements count="{len(statements)}">'
        + "".join(
            f'<FlexStatement accountId="U_TEST" reportDate="20260821">{statement}</FlexStatement>'
            for statement in statements
        )
        + "</FlexStatements></FlexQueryResponse>"
    ).encode()


@pytest.mark.parametrize("identifier", ["transactionID", "tradeID", "actionID"])
def test_distinct_lots_sharing_a_broker_identifier_keep_unique_refs(identifier):
    rows = job_raw_extract_payload_rows(_payload(
        f'<Trades><Lot {identifier}="100" quantity="2" costBasis="20" />'
        f'<Lot {identifier}="100" quantity="3" costBasis="45" />'
        f'<Trade {identifier}="100" quantity="5" />'
        f'<Lot {identifier}="200" quantity="1" /></Trades>'
    )).rows

    assert len({row.source_row_ref for row in rows}) == 4
    assert [row.source_payload["quantity"] for row in rows] == ["2", "3", "5", "1"]
    assert rows[2].source_row_ref == f"Trades:Trade:{identifier}=100"
    assert rows[3].source_row_ref == f"Trades:Lot:{identifier}=200"


def test_colliding_refs_are_stable_when_rows_and_attributes_are_reordered():
    first = job_raw_extract_payload_rows(_payload(
        '<Trades><Lot transactionID="100" quantity="2" />'
        '<Lot transactionID="100" quantity="3" />'
        '<Lot transactionID="100" quantity="2" /></Trades>'
    )).rows
    reordered = job_raw_extract_payload_rows(_payload(
        '<Trades><Lot quantity="2" transactionID="100" />'
        '<Lot quantity="2" transactionID="100" />'
        '<Lot quantity="3" transactionID="100" /></Trades>'
    )).rows

    assert len({row.source_row_ref for row in first}) == 3
    assert Counter((row.source_row_ref, row.source_payload["quantity"]) for row in first) == Counter(
        (row.source_row_ref, row.source_payload["quantity"]) for row in reordered
    )


def test_broker_identifiers_cannot_alias_generated_collision_refs():
    colliding_lots = (
        '<Lot transactionID="100" quantity="2" />'
        '<Lot transactionID="100" quantity="3" />'
    )
    initial_rows = job_raw_extract_payload_rows(_payload(f"<Trades>{colliding_lots}</Trades>")).rows
    prefix = "Trades:Lot:transactionID="
    broker_id = initial_rows[0].source_row_ref.removeprefix(prefix)
    adversarial_lot = f'<Lot transactionID={quoteattr(broker_id)} quantity="4" />'

    rows = job_raw_extract_payload_rows(_payload(
        f"<Trades>{colliding_lots}{adversarial_lot}</Trades>"
    )).rows
    reordered_rows = job_raw_extract_payload_rows(_payload(
        f"<Trades>{adversarial_lot}{colliding_lots}</Trades>"
    )).rows

    assert len({row.source_row_ref for row in rows}) == 3
    assert rows[2].source_row_ref == f"{prefix}{broker_id}"
    assert {row.source_payload["quantity"]: row.source_row_ref for row in rows} == {
        row.source_payload["quantity"]: row.source_row_ref for row in reordered_rows
    }


def test_collisions_across_statements_are_disambiguated_with_inherited_context():
    rows = job_raw_extract_payload_rows(_payload(
        '<Trades><Account id="U1"><Lot transactionID="100" quantity="2" /></Account></Trades>',
        '<Trades><Account id="U2"><Lot transactionID="100" quantity="3" /></Account></Trades>',
    )).rows

    assert len({row.source_row_ref for row in rows}) == 2
    assert [row.source_payload["id"] for row in rows] == ["U1", "U2"]


def test_persistence_keeps_all_lots_and_dividend_accruals_and_deduplicates_reimport(database):
    payload = _payload(
        '<Trades><Lot transactionID="100" quantity="2" costBasis="20" />'
        '<Lot transactionID="100" quantity="3" costBasis="45" />'
        '<Lot transactionID="100" quantity="2" costBasis="20" /></Trades>'
        '<ChangeInDividendAccruals>'
        '<ChangeInDividendAccrual actionID="200" code="Po" grossAmount="12" />'
        '<ChangeInDividendAccrual actionID="200" code="Re" grossAmount="-12" />'
        '</ChangeInDividendAccruals>'
    )
    runs = SQLAlchemyIngestionRunService(database)
    with runs.db_ingestion_run_guard("U_TEST"):
        run = runs.db_ingestion_run_create_started(
            account_id="U_TEST", run_type="manual", period_key="2026-08-21",
            flex_query_id="raw-identity", report_date_local=date(2026, 8, 21),
        )
    repository = SQLAlchemyRawPersistenceService(database)
    reference = RawArtifactReference(
        account_id="U_TEST", period_key="2026-08-21", flex_query_id="raw-identity",
        payload_sha256=sha256(payload).hexdigest(), report_date_local=date(2026, 8, 21),
    )
    for attempt in range(2):
        artifact = repository.db_raw_artifact_upsert(RawArtifactPersistRequest(
            ingestion_run_id=run.ingestion_run_id, reference=reference, source_payload=payload,
        ))
        extraction = job_raw_extract_payload_rows(payload)
        result = repository.db_raw_record_insert_many([
            RawRecordPersistRequest(
                ingestion_run_id=run.ingestion_run_id, raw_artifact_id=artifact.artifact.raw_artifact_id,
                artifact_reference=reference, report_date_local=extraction.report_date_local,
                section_name=row.section_name, source_row_ref=row.source_row_ref, source_payload=row.source_payload,
            )
            for row in extraction.rows
        ])
        assert artifact.deduplicated is (attempt == 1)
        assert (result.inserted_count, result.deduplicated_count) == ((5, 0) if attempt == 0 else (0, 5))

    with database.connect() as connection:
        persisted = connection.execute(text("SELECT section_name, source_payload FROM raw_record")).all()
        assert connection.scalar(text("SELECT count(*) FROM raw_artifact")) == 1
    assert Counter((section, payload.get("quantity"), payload.get("grossAmount")) for section, payload in persisted) == {
        ("Trades", "2", None): 2,
        ("Trades", "3", None): 1,
        ("ChangeInDividendAccruals", None, "12"): 1,
        ("ChangeInDividendAccruals", None, "-12"): 1,
    }
