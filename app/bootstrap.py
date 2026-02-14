"""Application bootstrap wiring for startup validation and dependency assembly."""

from fastapi import FastAPI

from app.api import create_api_application
from app.config import config_load_settings
from app.db import SQLAlchemyDatabaseHealthService, db_create_engine


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
    return create_api_application(settings=settings, db_health_service=db_health_service)
