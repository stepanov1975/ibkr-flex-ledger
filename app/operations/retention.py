"""Archive derived diagnostics before removing their hot database copies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path

from app.db import PortfolioRepositoryPort


@dataclass(frozen=True)
class DiagnosticRetentionResult:
    archive_path: str | None
    archived_count: int
    purged_count: int
    checksum_sha256: str | None


def operations_archive_expired_diagnostics(
    repository: PortfolioRepositoryPort,
    archive_root: Path,
    now_utc: datetime | None = None,
) -> DiagnosticRetentionResult:
    """Archive diagnostics older than 60 days, verify checksum, then purge hot payloads."""

    measured_now = now_utc or datetime.now(timezone.utc)
    if measured_now.tzinfo is None or measured_now.utcoffset() is None:
        raise ValueError("now_utc must be offset-aware")
    rows = repository.db_diagnostics_archive_candidates(measured_now - timedelta(days=60))
    if not rows:
        return DiagnosticRetentionResult(None, 0, 0, None)

    month_directory = archive_root / rows[0].started_at_utc.strftime("%Y-%m")
    month_directory.mkdir(parents=True, exist_ok=True)
    archive_path = month_directory / f"diagnostics-{measured_now.strftime('%Y%m%dT%H%M%SZ')}.jsonl.gz"
    with gzip.open(archive_path, "wt", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            payload = asdict(row)
            payload["ingestion_run_id"] = str(row.ingestion_run_id)
            payload["started_at_utc"] = row.started_at_utc.isoformat()
            stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")

    archive_bytes = archive_path.read_bytes()
    checksum = hashlib.sha256(archive_bytes).hexdigest()
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(f"{checksum}  {archive_path.name}\n", encoding="utf-8")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != checksum:
        raise RuntimeError("diagnostic archive checksum verification failed")

    purged_count = repository.db_diagnostics_purge([row.ingestion_run_id for row in rows])
    return DiagnosticRetentionResult(str(archive_path), len(rows), purged_count, checksum)
