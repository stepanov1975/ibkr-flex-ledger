"""FastAPI application factory for runtime foundation.

This module defines API application composition used by the MVP runtime.
"""

from fastapi import FastAPI

from app.config import AppSettings
from app.db import DatabaseHealthPort

from .routers import api_create_health_router


def create_api_application(settings: AppSettings, db_health_service: DatabaseHealthPort) -> FastAPI:
    """Create the FastAPI application instance for the service.

    Args:
        settings: Validated application settings used for runtime metadata.
        db_health_service: Database health service used by health endpoints.

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

    return application

