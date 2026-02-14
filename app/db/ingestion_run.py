"""Database service for ingestion run lifecycle persistence and lock enforcement."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from .interfaces import (
    IngestionRunAlreadyActiveError,
    IngestionRunRecord,
    IngestionRunReference,
    IngestionRunRepositoryPort,
    IngestionRunState,
)


class SQLAlchemyIngestionRunService(IngestionRunRepositoryPort):
    """SQLAlchemy-backed ingestion run service.

    This service centralizes ingestion run write/read operations in the db layer,
    including single-active-run lock enforcement.
    """

    def __init__(self, engine: Engine):
        """Initialize ingestion run persistence service.

        Args:
            engine: SQLAlchemy engine used for all persistence operations.

        Returns:
            None: This initializer does not return a value.

        Raises:
            ValueError: Raised when engine is None.
        """

        if engine is None:
            raise ValueError("engine must not be None")
        self._engine = engine

    def db_ingestion_run_create_started(
        self,
        account_id: str,
        run_type: str,
        period_key: str,
        flex_query_id: str,
        report_date_local: date | None,
    ) -> IngestionRunRecord:
        """Create a started run while enforcing a single active run per account.

        Args:
            account_id: Internal account identifier.
            run_type: Trigger source (`scheduled`, `manual`, `reprocess`).
            period_key: Ingestion period identity key.
            flex_query_id: Upstream Flex query identifier.
            report_date_local: Optional local report date.

        Returns:
            IngestionRunRecord: Newly created started run.

        Raises:
            IngestionRunAlreadyActiveError: Raised when lock cannot be obtained or active run exists.
            ValueError: Raised when required inputs are blank.
            RuntimeError: Raised when persistence fails.
        """

        normalized_account_id = self._validate_non_empty_text(account_id, "account_id")
        normalized_run_type = self._validate_non_empty_text(run_type, "run_type")
        normalized_period_key = self._validate_non_empty_text(period_key, "period_key")
        normalized_flex_query_id = self._validate_non_empty_text(flex_query_id, "flex_query_id")

        advisory_key_1, advisory_key_2 = self._build_advisory_lock_keys(normalized_account_id)

        try:
            with self._engine.begin() as connection:
                lock_row = connection.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key_1, :key_2) AS lock_acquired"),
                    {"key_1": advisory_key_1, "key_2": advisory_key_2},
                ).mappings().one()
                if not bool(lock_row["lock_acquired"]):
                    raise IngestionRunAlreadyActiveError("run already active")

                active_row = connection.execute(
                    text(
                        "SELECT ingestion_run_id "
                        "FROM ingestion_run "
                        "WHERE account_id = :account_id AND status = 'started' "
                        "LIMIT 1"
                    ),
                    {"account_id": normalized_account_id},
                ).first()
                if active_row is not None:
                    raise IngestionRunAlreadyActiveError("run already active")

                created_row = connection.execute(
                    text(
                        "INSERT INTO ingestion_run ("
                        "account_id, run_type, status, period_key, flex_query_id, report_date_local, started_at_utc"
                        ") VALUES ("
                        ":account_id, :run_type, 'started', :period_key, :flex_query_id, :report_date_local, now()"
                        ") "
                        "RETURNING ingestion_run_id"
                    ),
                    {
                        "account_id": normalized_account_id,
                        "run_type": normalized_run_type,
                        "period_key": normalized_period_key,
                        "flex_query_id": normalized_flex_query_id,
                        "report_date_local": report_date_local,
                    },
                ).mappings().one()

                created_run_id = created_row["ingestion_run_id"]
                return self._db_fetch_run_by_id_or_raise(connection=connection, ingestion_run_id=created_run_id)
        except SQLAlchemyError as error:
            raise RuntimeError("failed to create started ingestion run") from error

    def db_ingestion_run_finalize(
        self,
        ingestion_run_id: UUID,
        status: str,
        error_code: str | None,
        error_message: str | None,
        diagnostics: list[dict[str, Any]] | None,
    ) -> IngestionRunRecord:
        """Finalize one run with deterministic end timestamp and duration.

        Args:
            ingestion_run_id: Run identifier.
            status: Final status (`success` or `failed`).
            error_code: Optional deterministic error code.
            error_message: Optional human-readable message.
            diagnostics: Optional structured diagnostics payload.

        Returns:
            IngestionRunRecord: Finalized run row.

        Raises:
            LookupError: Raised when run is not found.
            ValueError: Raised when final status is invalid.
            RuntimeError: Raised when persistence fails.
        """

        if status not in {"success", "failed"}:
            raise ValueError("status must be one of: success, failed")

        diagnostics_payload = None
        if diagnostics is not None:
            diagnostics_payload = json.dumps(diagnostics)

        try:
            with self._engine.begin() as connection:
                updated_row = connection.execute(
                    text(
                        "UPDATE ingestion_run SET "
                        "status = :status, "
                        "ended_at_utc = now(), "
                        "duration_ms = GREATEST(0, CAST(EXTRACT(EPOCH FROM (now() - started_at_utc)) * 1000 AS BIGINT)), "
                        "error_code = :error_code, "
                        "error_message = :error_message, "
                        "diagnostics = CAST(:diagnostics AS jsonb) "
                        "WHERE ingestion_run_id = :ingestion_run_id "
                        "RETURNING ingestion_run_id"
                    ),
                    {
                        "status": status,
                        "error_code": error_code,
                        "error_message": error_message,
                        "diagnostics": diagnostics_payload,
                        "ingestion_run_id": ingestion_run_id,
                    },
                ).mappings().first()
                if updated_row is None:
                    raise LookupError("ingestion run not found")

                return self._db_fetch_run_by_id_or_raise(connection=connection, ingestion_run_id=ingestion_run_id)
        except SQLAlchemyError as error:
            raise RuntimeError("failed to finalize ingestion run") from error

    def db_ingestion_run_get_by_id(self, ingestion_run_id: UUID) -> IngestionRunRecord | None:
        """Fetch one ingestion run by id.

        Args:
            ingestion_run_id: Run identifier.

        Returns:
            IngestionRunRecord | None: Matching run row or None.

        Raises:
            RuntimeError: Raised when database read fails.
        """

        try:
            with self._engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT "
                        "ingestion_run_id, account_id, run_type, status, period_key, flex_query_id, "
                        "report_date_local, started_at_utc, ended_at_utc, duration_ms, "
                        "error_code, error_message, diagnostics, created_at_utc "
                        "FROM ingestion_run "
                        "WHERE ingestion_run_id = :ingestion_run_id"
                    ),
                    {"ingestion_run_id": ingestion_run_id},
                ).mappings().first()
                if row is None:
                    return None
                return self._map_ingestion_run_record(row)
        except SQLAlchemyError as error:
            raise RuntimeError("failed to fetch ingestion run by id") from error

    def db_ingestion_run_list(self, limit: int, offset: int) -> list[IngestionRunRecord]:
        """List runs with deterministic default ordering.

        Args:
            limit: Maximum number of rows.
            offset: Number of rows to skip.

        Returns:
            list[IngestionRunRecord]: Ordered run rows.

        Raises:
            ValueError: Raised when limit or offset are invalid.
            RuntimeError: Raised when database read fails.
        """

        if limit < 1:
            raise ValueError("limit must be >= 1")
        if offset < 0:
            raise ValueError("offset must be >= 0")

        try:
            with self._engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT "
                        "ingestion_run_id, account_id, run_type, status, period_key, flex_query_id, "
                        "report_date_local, started_at_utc, ended_at_utc, duration_ms, "
                        "error_code, error_message, diagnostics, created_at_utc "
                        "FROM ingestion_run "
                        "ORDER BY started_at_utc DESC, ingestion_run_id DESC "
                        "LIMIT :limit OFFSET :offset"
                    ),
                    {"limit": limit, "offset": offset},
                ).mappings().all()

                return [self._map_ingestion_run_record(row) for row in rows]
        except SQLAlchemyError as error:
            raise RuntimeError("failed to list ingestion runs") from error

    def _db_fetch_run_by_id_or_raise(self, connection, ingestion_run_id: UUID) -> IngestionRunRecord:
        """Fetch one run inside active transaction and raise when missing.

        Args:
            connection: Active SQLAlchemy connection.
            ingestion_run_id: Run identifier.

        Returns:
            IngestionRunRecord: Matching row.

        Raises:
            LookupError: Raised when row cannot be found.
        """

        row = connection.execute(
            text(
                "SELECT "
                "ingestion_run_id, account_id, run_type, status, period_key, flex_query_id, "
                "report_date_local, started_at_utc, ended_at_utc, duration_ms, "
                "error_code, error_message, diagnostics, created_at_utc "
                "FROM ingestion_run "
                "WHERE ingestion_run_id = :ingestion_run_id"
            ),
            {"ingestion_run_id": ingestion_run_id},
        ).mappings().first()
        if row is None:
            raise LookupError("ingestion run not found")
        return self._map_ingestion_run_record(row)

    def _map_ingestion_run_record(self, row: Any) -> IngestionRunRecord:
        """Map SQLAlchemy row mapping to typed ingestion run record.

        Args:
            row: SQLAlchemy mapping row.

        Returns:
            IngestionRunRecord: Typed run record.

        Raises:
            TypeError: Raised when row structure is incompatible.
        """

        diagnostics_value = row["diagnostics"]
        if diagnostics_value is not None and not isinstance(diagnostics_value, list):
            raise TypeError("ingestion_run.diagnostics must be a JSON array when present")

        return IngestionRunRecord(
            ingestion_run_id=row["ingestion_run_id"],
            account_id=row["account_id"],
            run_type=row["run_type"],
            reference=IngestionRunReference(
                period_key=row["period_key"],
                flex_query_id=row["flex_query_id"],
                report_date_local=row["report_date_local"],
            ),
            state=IngestionRunState(
                status=row["status"],
                started_at_utc=row["started_at_utc"],
                ended_at_utc=row["ended_at_utc"],
                duration_ms=row["duration_ms"],
                error_code=row["error_code"],
                error_message=row["error_message"],
                diagnostics=diagnostics_value,
            ),
            created_at_utc=row["created_at_utc"],
        )

    def _build_advisory_lock_keys(self, account_id: str) -> tuple[int, int]:
        """Create deterministic advisory lock keys for account-scoped run lock.

        Args:
            account_id: Internal account identifier.

        Returns:
            tuple[int, int]: Two signed int32 lock keys for PostgreSQL advisory lock.

        Raises:
            ValueError: Raised when account_id is blank.
        """

        normalized_account_id = self._validate_non_empty_text(account_id, "account_id")
        digest = hashlib.sha256(normalized_account_id.encode("utf-8")).digest()
        key_1 = int.from_bytes(digest[0:4], byteorder="big", signed=True)
        key_2 = int.from_bytes(digest[4:8], byteorder="big", signed=True)
        return key_1, key_2

    def _validate_non_empty_text(self, value: str, field_name: str) -> str:
        """Validate required text input and return stripped value.

        Args:
            value: Candidate string value.
            field_name: Field name for error reporting.

        Returns:
            str: Stripped non-empty value.

        Raises:
            ValueError: Raised when value is blank.
        """

        stripped_value = value.strip()
        if not stripped_value:
            raise ValueError(f"{field_name} must not be blank")
        return stripped_value
