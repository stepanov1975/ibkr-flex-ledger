"""FastAPI application factory for runtime foundation.

This module defines API application composition used by the MVP runtime.
"""

from fastapi import FastAPI

from app.config import AppSettings
from app.db import DatabaseHealthPort, IngestionRunRepositoryPort
from app.jobs import JobOrchestratorPort

from .routers import api_create_health_router, api_create_ingestion_router


def create_api_application(
    settings: AppSettings,
    db_health_service: DatabaseHealthPort,
    ingestion_repository: IngestionRunRepositoryPort,
    ingestion_orchestrator: JobOrchestratorPort,
    reprocess_orchestrator: JobOrchestratorPort | None = None,
) -> FastAPI:
    """Create the FastAPI application instance for the service.

    Args:
        settings: Validated application settings used for runtime metadata.
        db_health_service: Database health service used by health endpoints.
        ingestion_repository: Ingestion run repository for list/detail APIs.
        ingestion_orchestrator: Job orchestrator for ingestion trigger execution.
        reprocess_orchestrator: Optional job orchestrator for reprocess trigger execution.

    Returns:
        FastAPI: Framework application instance with foundation metadata.

    Raises:
        RuntimeError: Raised if application initialization fails.
    """
    application = FastAPI(title="IBKR Flex Ledger")

    @application.get("/", tags=["foundation"])
    def foundation_index() -> dict[str, str]:
        """Return a minimal foundation response for bootstrap verification.

        Returns:
            dict[str, str]: Minimal response for API framework verification.

        Raises:
            RuntimeError: Raised if route handler cannot produce a response.
        """

        return {
            "service": "ibkr-flex-ledger",
            "status": "foundation-ready",
            "environment": settings.environment_name,
        }

    application.include_router(api_create_health_router(db_health_service=db_health_service))
    application.include_router(
        api_create_ingestion_router(
            settings=settings,
            ingestion_repository=ingestion_repository,
            ingestion_orchestrator=ingestion_orchestrator,
            reprocess_orchestrator=reprocess_orchestrator,
        )
    )

    return application

