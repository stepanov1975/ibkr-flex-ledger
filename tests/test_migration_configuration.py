"""Run the real offline migration entry point with escaped URL credentials."""

import os
from pathlib import Path
import subprocess
import sys


def test_migrations_accept_percent_encoded_credentials() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260911_16", "--sql"],
        cwd=Path(__file__).parents[1],
        env={**os.environ, "DATABASE_URL": "postgresql+psycopg://user:pass%40word%25@localhost/db"},
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE" in result.stdout


def test_broker_identity_backfill_rejects_offline_migration() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=Path(__file__).parents[1],
        env={**os.environ, "DATABASE_URL": "postgresql+psycopg://user:password@localhost/db"},
        text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "broker account identity backfill requires an online migration" in result.stderr
