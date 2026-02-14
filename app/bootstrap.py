"""Application bootstrap wiring for startup validation and dependency assembly."""

from fastapi import FastAPI

from app.api import create_api_application
from app.config import config_load_settings
from app.adapters import FlexWebServiceAdapter
from app.db import SQLAlchemyDatabaseHealthService, SQLAlchemyIngestionRunService, db_create_engine
from app.jobs import IngestionJobOrchestrator, IngestionOrchestratorConfig


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
    flex_adapter = FlexWebServiceAdapter(token=settings.ibkr_flex_token)
    ingestion_orchestrator = IngestionJobOrchestrator(
        ingestion_repository=ingestion_repository,
        flex_adapter=flex_adapter,
        config=IngestionOrchestratorConfig(
            account_id=settings.account_id,
            flex_query_id=settings.ibkr_flex_query_id,
            run_type="manual",
            reconciliation_enabled=False,
        ),
    )
    return create_api_application(
        settings=settings,
        db_health_service=db_health_service,
        ingestion_repository=ingestion_repository,
        ingestion_orchestrator=ingestion_orchestrator,
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
    flex_adapter = FlexWebServiceAdapter(token=settings.ibkr_flex_token)
    return IngestionJobOrchestrator(
        ingestion_repository=ingestion_repository,
        flex_adapter=flex_adapter,
        config=IngestionOrchestratorConfig(
            account_id=settings.account_id,
            flex_query_id=settings.ibkr_flex_query_id,
            run_type="manual",
            reconciliation_enabled=False,
        ),
    )
