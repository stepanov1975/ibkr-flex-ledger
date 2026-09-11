"""Persist broker account identity without rereading immutable report payloads."""

import xml.etree.ElementTree as ET

from alembic import op
import sqlalchemy as sa


revision = "20260911_17"
down_revision = "20260911_16"
branch_labels = None
depends_on = None

def _backfill_broker_account_ids() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT artifact.raw_artifact_id, artifact.source_payload FROM raw_artifact artifact "
            "JOIN ingestion_run owner ON owner.ingestion_run_id=artifact.ingestion_run_id "
            "LEFT JOIN ingestion_run completed "
            "ON completed.ingestion_run_id=artifact.completed_ingestion_run_id "
            "WHERE artifact.broker_account_id IS NULL "
            "AND ((artifact.completed_ingestion_run_id IS NOT NULL AND completed.status='success') "
            "OR (artifact.completed_ingestion_run_id IS NULL AND owner.status='success')) "
            "ORDER BY artifact.raw_artifact_id"
        ).execution_options(stream_results=True)
    ).mappings().yield_per(1)
    for row in rows:
        raw_artifact_id = row["raw_artifact_id"]
        try:
            root = ET.fromstring(bytes(row["source_payload"]))
        except ET.ParseError as error:
            raise RuntimeError(
                f"successful raw artifact {raw_artifact_id} has unreadable broker account metadata"
            ) from error

        identities: set[str] = set()
        for element in root.iter():
            if "accountId" not in element.attrib:
                continue
            broker_account_id = element.attrib["accountId"].strip()
            if not broker_account_id:
                raise RuntimeError(
                    f"successful raw artifact {raw_artifact_id} has blank broker account metadata"
                )
            identities.add(broker_account_id)
        if len(identities) != 1:
            raise RuntimeError(
                f"successful raw artifact {raw_artifact_id} must contain exactly one broker account ID; "
                f"found {len(identities)}"
            )

        connection.execute(
            sa.text(
                "UPDATE raw_artifact SET broker_account_id=:broker_account_id "
                "WHERE raw_artifact_id=:raw_artifact_id"
            ),
            {
                "raw_artifact_id": raw_artifact_id,
                "broker_account_id": next(iter(identities)),
            },
        )


def upgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("broker account identity backfill requires an online migration")
    op.add_column(
        "raw_artifact",
        sa.Column("broker_account_id", sa.Text(), nullable=True),
    )
    _backfill_broker_account_ids()


def downgrade() -> None:
    op.drop_column("raw_artifact", "broker_account_id")
