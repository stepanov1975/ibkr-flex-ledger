"""Derived diagnostic retention tests."""

from datetime import datetime, timedelta, timezone
import gzip
import json
from uuid import UUID, uuid4

from app.db import DiagnosticArchiveRecord
from app.operations import operations_archive_expired_diagnostics


class _RetentionRepository:
    def __init__(self, row: DiagnosticArchiveRecord):
        self.row = row
        self.purged: list[UUID] = []

    def db_diagnostics_archive_candidates(self, cutoff_utc: datetime) -> list[DiagnosticArchiveRecord]:
        return [self.row] if self.row.started_at_utc < cutoff_utc else []

    def db_diagnostics_purge(self, ingestion_run_ids: list[UUID]) -> int:
        self.purged.extend(ingestion_run_ids)
        return len(ingestion_run_ids)


def test_retention_archives_and_verifies_before_purge(tmp_path) -> None:
    """Write compressed JSONL and checksum before clearing hot diagnostics."""

    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    row = DiagnosticArchiveRecord(
        ingestion_run_id=uuid4(), account_id="U1", run_type="manual", status="failed",
        started_at_utc=now - timedelta(days=61), diagnostics=[{"error_code": "TEST"}],
    )
    repository = _RetentionRepository(row)

    result = operations_archive_expired_diagnostics(repository, tmp_path, now)

    assert result.archived_count == 1
    assert result.purged_count == 1
    assert repository.purged == [row.ingestion_run_id]
    assert result.archive_path is not None
    with gzip.open(result.archive_path, "rt", encoding="utf-8") as stream:
        payload = json.loads(stream.readline())
    assert payload["diagnostics"][0]["error_code"] == "TEST"
