"""Application bootstrap wiring for startup validation and dependency assembly."""

from datetime import datetime, timezone

from fastapi import FastAPI

from app.api import create_api_application
from app.config import config_load_settings
from app.adapters import FlexWebServiceAdapter
from app.db import (
    SQLAlchemyCanonicalPersistenceService,
    SQLAlchemyDatabaseHealthService,
    SQLAlchemyIngestionRunService,
    SQLAlchemyLedgerSnapshotService,
    SQLAlchemyRawPersistenceService,
    db_create_engine,
)
from app.jobs import (
    CanonicalReprocessOrchestrator,
    CanonicalReprocessOrchestratorConfig,
    IngestionJobOrchestrator,
    IngestionOrchestratorConfig,
)
from app.ledger import StockLedgerSnapshotService


def bootstrap_create_application() -> FastAPI:
    """Assemble the runtime application after validating startup configuration.

    Returns:
        FastAPI: Fully initialized FastAPI application instance.

    Raises:
        SettingsLoadError: Raised when startup configuration validation fails.
    """

    settings = config_load_settings()
    engine = db_create_engine(database_url=settings.database_url)
    db_health_service = SQLAlchemyDatabaseHealthService(engine=engine)
    ingestion_repository = SQLAlchemyIngestionRunService(engine=engine)
    raw_persistence_repository = SQLAlchemyRawPersistenceService(engine=engine)
    canonical_repository = SQLAlchemyCanonicalPersistenceService(engine=engine)
    snapshot_repository = SQLAlchemyLedgerSnapshotService(engine=engine)
    snapshot_service = StockLedgerSnapshotService(repository=snapshot_repository)
    flex_adapter = FlexWebServiceAdapter(
        token=settings.ibkr_flex_token,
        initial_wait_seconds=settings.ibkr_flex_initial_wait_seconds,
        retry_attempts=settings.ibkr_flex_retry_attempts,
        retry_backoff_base_seconds=settings.ibkr_flex_backoff_base_seconds,
        retry_max_backoff_seconds=settings.ibkr_flex_backoff_max_seconds,
        jitter_min_multiplier=settings.ibkr_flex_jitter_min_multiplier,
        jitter_max_multiplier=settings.ibkr_flex_jitter_max_multiplier,
    )
    ingestion_orchestrator = IngestionJobOrchestrator(
        ingestion_repository=ingestion_repository,
        raw_persistence_repository=raw_persistence_repository,
        flex_adapter=flex_adapter,
        config=IngestionOrchestratorConfig(
            account_id=settings.account_id,
            flex_query_id=settings.ibkr_flex_query_id,
            run_type="manual",
            reconciliation_enabled=False,
            functional_currency="USD",
        ),
        canonical_repository=canonical_repository,
        snapshot_service=snapshot_service,
    )
    reprocess_orchestrator = CanonicalReprocessOrchestrator(
        raw_read_repository=canonical_repository,
        canonical_persistence_repository=canonical_repository,
        ingestion_repository=ingestion_repository,
        config=CanonicalReprocessOrchestratorConfig(
            account_id=settings.account_id,
            period_key=datetime.now(timezone.utc).date().isoformat(),
            flex_query_id=settings.ibkr_flex_query_id,
            functional_currency="USD",
        ),
    )
    return create_api_application(
        settings=settings,
        db_health_service=db_health_service,
        ingestion_repository=ingestion_repository,
        ingestion_orchestrator=ingestion_orchestrator,
        reprocess_orchestrator=reprocess_orchestrator,
        snapshot_repository=snapshot_repository,
    )


def bootstrap_create_ingestion_orchestrator() -> IngestionJobOrchestrator:
    """Build ingestion orchestrator for non-HTTP trigger surfaces.

    Returns:
        IngestionJobOrchestrator: Fully wired ingestion orchestrator instance.

    Raises:
        SettingsLoadError: Raised when startup configuration validation fails.
    """

    settings = config_load_settings()
    engine = db_create_engine(database_url=settings.database_url)
    ingestion_repository = SQLAlchemyIngestionRunService(engine=engine)
    raw_persistence_repository = SQLAlchemyRawPersistenceService(engine=engine)
    canonical_repository = SQLAlchemyCanonicalPersistenceService(engine=engine)
    snapshot_repository = SQLAlchemyLedgerSnapshotService(engine=engine)
    snapshot_service = StockLedgerSnapshotService(repository=snapshot_repository)
    flex_adapter = FlexWebServiceAdapter(
        token=settings.ibkr_flex_token,
        initial_wait_seconds=settings.ibkr_flex_initial_wait_seconds,
        retry_attempts=settings.ibkr_flex_retry_attempts,
        retry_backoff_base_seconds=settings.ibkr_flex_backoff_base_seconds,
        retry_max_backoff_seconds=settings.ibkr_flex_backoff_max_seconds,
        jitter_min_multiplier=settings.ibkr_flex_jitter_min_multiplier,
        jitter_max_multiplier=settings.ibkr_flex_jitter_max_multiplier,
    )
    return IngestionJobOrchestrator(
        ingestion_repository=ingestion_repository,
        raw_persistence_repository=raw_persistence_repository,
        flex_adapter=flex_adapter,
        config=IngestionOrchestratorConfig(
            account_id=settings.account_id,
            flex_query_id=settings.ibkr_flex_query_id,
            run_type="manual",
            reconciliation_enabled=False,
            functional_currency="USD",
        ),
        canonical_repository=canonical_repository,
        snapshot_service=snapshot_service,
    )


def bootstrap_create_reprocess_orchestrator(
    period_key: str | None = None,
    flex_query_id: str | None = None,
) -> CanonicalReprocessOrchestrator:
    """Build canonical reprocess orchestrator for non-HTTP trigger surfaces.

    Returns:
        CanonicalReprocessOrchestrator: Fully wired canonical reprocess orchestrator instance.

    Raises:
        SettingsLoadError: Raised when startup configuration validation fails.
    """

    settings = config_load_settings()
    resolved_period_key = (period_key or datetime.now(timezone.utc).date().isoformat()).strip()
    resolved_flex_query_id = (flex_query_id or settings.ibkr_flex_query_id).strip()
    engine = db_create_engine(database_url=settings.database_url)
    ingestion_repository = SQLAlchemyIngestionRunService(engine=engine)
    canonical_repository = SQLAlchemyCanonicalPersistenceService(engine=engine)
    return CanonicalReprocessOrchestrator(
        raw_read_repository=canonical_repository,
        canonical_persistence_repository=canonical_repository,
        ingestion_repository=ingestion_repository,
        config=CanonicalReprocessOrchestratorConfig(
            account_id=settings.account_id,
            period_key=resolved_period_key,
            flex_query_id=resolved_flex_query_id,
            functional_currency="USD",
        ),
    )
