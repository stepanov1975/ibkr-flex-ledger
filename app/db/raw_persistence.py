"""Database service for immutable raw artifact and raw row persistence."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.db.interfaces import (
    RawArtifactPersistRequest,
    RawArtifactPersistResult,
    RawArtifactRecord,
    RawArtifactReference,
    RawPersistenceRepositoryPort,
    RawRecordPersistRequest,
    RawRecordPersistResult,
)


class SQLAlchemyRawPersistenceService(RawPersistenceRepositoryPort):
    """SQLAlchemy implementation of immutable raw persistence operations."""

    def __init__(self, engine: Engine):
        """Initialize raw persistence service.

        Args:
            engine: SQLAlchemy engine used for all persistence operations.

        Returns:
            None: Initializer does not return values.

        Raises:
            ValueError: Raised when engine is invalid.
        """

        if engine is None:
            raise ValueError("engine must not be None")

        self._engine = engine

    def db_raw_artifact_upsert(self, request: RawArtifactPersistRequest) -> RawArtifactPersistResult:
        """Persist or reuse immutable raw artifact by dedupe identity key.

        Args:
            request: Raw artifact persistence request payload.

        Returns:
            RawArtifactPersistResult: Persisted artifact row with dedupe indicator.

        Raises:
            ValueError: Raised when request values are invalid.
            RuntimeError: Raised when persistence operation fails.
        """

        normalized_reference = self._db_raw_validate_reference(request.reference)
        if not isinstance(request.source_payload, bytes):
            raise ValueError("request.source_payload must be bytes")

        try:
            with self._engine.begin() as connection:
                inserted_row = connection.execute(
                    text(
                        "INSERT INTO raw_artifact ("
                        "ingestion_run_id, account_id, period_key, flex_query_id, payload_sha256, report_date_local, source_payload"
                        ") VALUES ("
                        ":ingestion_run_id, :account_id, :period_key, :flex_query_id, :payload_sha256, :report_date_local, :source_payload"
                        ") "
                        "ON CONFLICT (account_id, period_key, flex_query_id, payload_sha256) DO NOTHING "
                        "RETURNING raw_artifact_id, ingestion_run_id, account_id, period_key, flex_query_id, "
                        "payload_sha256, report_date_local, source_payload, created_at_utc"
                    ),
                    {
                        "ingestion_run_id": request.ingestion_run_id,
                        "account_id": normalized_reference.account_id,
                        "period_key": normalized_reference.period_key,
                        "flex_query_id": normalized_reference.flex_query_id,
                        "payload_sha256": normalized_reference.payload_sha256,
                        "report_date_local": normalized_reference.report_date_local,
                        "source_payload": request.source_payload,
                    },
                ).mappings().fetchone()

                if inserted_row is not None:
                    return RawArtifactPersistResult(
                        artifact=self._db_raw_map_artifact_row(inserted_row),
                        deduplicated=False,
                    )

                reused_row = connection.execute(
                    text(
                        "SELECT raw_artifact_id, ingestion_run_id, account_id, period_key, flex_query_id, "
                        "payload_sha256, report_date_local, source_payload, created_at_utc "
                        "FROM raw_artifact "
                        "WHERE account_id = :account_id AND period_key = :period_key "
                        "AND flex_query_id = :flex_query_id AND payload_sha256 = :payload_sha256"
                    ),
                    {
                        "account_id": normalized_reference.account_id,
                        "period_key": normalized_reference.period_key,
                        "flex_query_id": normalized_reference.flex_query_id,
                        "payload_sha256": normalized_reference.payload_sha256,
                    },
                ).mappings().fetchone()

                if reused_row is None:
                    raise RuntimeError("raw artifact upsert conflict occurred without existing row")

                return RawArtifactPersistResult(
                    artifact=self._db_raw_map_artifact_row(reused_row),
                    deduplicated=True,
                )
        except SQLAlchemyError as error:
            raise RuntimeError("raw artifact persistence failed") from error

    def db_raw_record_insert_many(self, requests: list[RawRecordPersistRequest]) -> RawRecordPersistResult:
        """Insert raw rows with deterministic unique-key dedupe behavior.

        Args:
            requests: Raw row persistence requests.

        Returns:
            RawRecordPersistResult: Inserted and deduplicated row counters.

        Raises:
            ValueError: Raised when requests are invalid.
            RuntimeError: Raised when persistence operation fails.
        """

        if requests is None:
            raise ValueError("requests must not be None")
        if len(requests) == 0:
            return RawRecordPersistResult(inserted_count=0, deduplicated_count=0)

        inserted_count = 0
        deduplicated_count = 0

        try:
            with self._engine.begin() as connection:
                for request in requests:
                    normalized_request = self._db_raw_validate_row_request(request)
                    inserted_row = connection.execute(
                        text(
                            "INSERT INTO raw_record ("
                            "raw_artifact_id, ingestion_run_id, account_id, period_key, flex_query_id, payload_sha256, "
                            "report_date_local, section_name, source_row_ref, source_payload"
                            ") VALUES ("
                            ":raw_artifact_id, :ingestion_run_id, :account_id, :period_key, :flex_query_id, :payload_sha256, "
                            ":report_date_local, :section_name, :source_row_ref, CAST(:source_payload AS jsonb)"
                            ") ON CONFLICT ON CONSTRAINT uq_raw_record_artifact_section_source_ref DO NOTHING "
                            "RETURNING raw_record_id"
                        ),
                        {
                            "raw_artifact_id": normalized_request.raw_artifact_id,
                            "ingestion_run_id": normalized_request.ingestion_run_id,
                            "account_id": normalized_request.artifact_reference.account_id,
                            "period_key": normalized_request.artifact_reference.period_key,
                            "flex_query_id": normalized_request.artifact_reference.flex_query_id,
                            "payload_sha256": normalized_request.artifact_reference.payload_sha256,
                            "report_date_local": normalized_request.report_date_local,
                            "section_name": normalized_request.section_name,
                            "source_row_ref": normalized_request.source_row_ref,
                            "source_payload": json.dumps(normalized_request.source_payload),
                        },
                    ).mappings().fetchone()

                    if inserted_row is None:
                        deduplicated_count += 1
                    else:
                        inserted_count += 1

                return RawRecordPersistResult(inserted_count=inserted_count, deduplicated_count=deduplicated_count)
        except SQLAlchemyError as error:
            raise RuntimeError("raw row persistence failed") from error

    def _db_raw_validate_reference(self, reference: RawArtifactReference) -> RawArtifactReference:
        """Validate and normalize raw artifact identity fields.

        Args:
            reference: Raw artifact identity values.

        Returns:
            RawArtifactReference: Normalized reference values.

        Raises:
            ValueError: Raised when required values are missing.
        """

        if reference is None:
            raise ValueError("request.reference must not be None")

        account_id = self._db_raw_validate_non_empty_text(reference.account_id, "reference.account_id")
        period_key = self._db_raw_validate_non_empty_text(reference.period_key, "reference.period_key")
        flex_query_id = self._db_raw_validate_non_empty_text(reference.flex_query_id, "reference.flex_query_id")
        payload_sha256 = self._db_raw_validate_non_empty_text(reference.payload_sha256, "reference.payload_sha256")

        return RawArtifactReference(
            account_id=account_id,
            period_key=period_key,
            flex_query_id=flex_query_id,
            payload_sha256=payload_sha256,
            report_date_local=reference.report_date_local,
        )

    def _db_raw_validate_row_request(self, request: RawRecordPersistRequest) -> RawRecordPersistRequest:
        """Validate and normalize one raw row persistence request.

        Args:
            request: Raw row request.

        Returns:
            RawRecordPersistRequest: Normalized request values.

        Raises:
            ValueError: Raised when required fields are invalid.
        """

        if request is None:
            raise ValueError("request must not be None")

        return RawRecordPersistRequest(
            ingestion_run_id=request.ingestion_run_id,
            raw_artifact_id=request.raw_artifact_id,
            artifact_reference=self._db_raw_validate_reference(request.artifact_reference),
            report_date_local=request.report_date_local,
            section_name=self._db_raw_validate_non_empty_text(request.section_name, "request.section_name"),
            source_row_ref=self._db_raw_validate_non_empty_text(request.source_row_ref, "request.source_row_ref"),
            source_payload=request.source_payload,
        )

    def _db_raw_map_artifact_row(self, row: Any) -> RawArtifactRecord:
        """Map SQL row payload into typed raw artifact record.

        Args:
            row: SQLAlchemy row mapping.

        Returns:
            RawArtifactRecord: Typed raw artifact persistence record.

        Raises:
            TypeError: Raised when `source_payload` is not bytes.
        """

        source_payload = row["source_payload"]
        if not isinstance(source_payload, bytes):
            raise TypeError("raw_artifact.source_payload must be bytes")

        return RawArtifactRecord(
            raw_artifact_id=row["raw_artifact_id"],
            ingestion_run_id=row["ingestion_run_id"],
            reference=RawArtifactReference(
                account_id=row["account_id"],
                period_key=row["period_key"],
                flex_query_id=row["flex_query_id"],
                payload_sha256=row["payload_sha256"],
                report_date_local=row["report_date_local"],
            ),
            source_payload=source_payload,
            created_at_utc=row["created_at_utc"],
        )

    def _db_raw_validate_non_empty_text(self, value: str, field_name: str) -> str:
        """Validate text value and normalize surrounding whitespace.

        Args:
            value: Input text value.
            field_name: Field label for deterministic error messages.

        Returns:
            str: Normalized non-empty text value.

        Raises:
            ValueError: Raised when value is not valid non-empty text.
        """

        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")

        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError(f"{field_name} must not be blank")

        return normalized_value
