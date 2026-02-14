"""API router package for endpoint composition."""

from .health import api_create_health_router
from .ingestion import api_create_ingestion_router

__all__ = ["api_create_health_router", "api_create_ingestion_router"]
