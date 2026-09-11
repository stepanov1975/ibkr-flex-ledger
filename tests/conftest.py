"""Pytest configuration for project test path setup."""

from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def legacy_partial_ingestion(monkeypatch):
    """Recreate pre-upgrade writes for tests of retained legacy data and replay.

    These selected tests exercise read models on data that older importers could
    leave behind. Current ingestion atomicity and strict consistency are tested
    separately; this fixture must never be applied to those workflow tests.
    """
    from contextlib import nullcontext
    from app.db import SQLAlchemyCanonicalPersistenceService, SQLAlchemyIngestionRunService

    monkeypatch.setattr(SQLAlchemyCanonicalPersistenceService, 'db_canonical_transaction', lambda self: nullcontext())
    monkeypatch.setattr(SQLAlchemyCanonicalPersistenceService, 'db_canonical_validate_trade_fills', lambda self, requests: None)
    monkeypatch.setattr(SQLAlchemyIngestionRunService, 'db_ingestion_run_guard', lambda self, account_id: nullcontext())


@pytest.fixture
def legacy_trade_corrections(monkeypatch):
    """Seed historically accepted execution corrections for replay regressions."""
    from app.db import SQLAlchemyCanonicalPersistenceService

    monkeypatch.setattr(SQLAlchemyCanonicalPersistenceService, 'db_canonical_validate_trade_fills', lambda self, requests: None)
