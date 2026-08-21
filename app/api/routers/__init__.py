"""API router package for endpoint composition."""

from .health import api_create_health_router
from .ingestion import api_create_ingestion_router
from .snapshot import api_create_snapshot_router
from .corporate_actions import api_create_corporate_action_router
from .master_data import api_create_master_data_router
from .operations import api_create_operations_router
from .reports import api_create_reports_router
from .ui import api_create_ui_router

__all__ = [
    "api_create_corporate_action_router",
    "api_create_health_router",
    "api_create_ingestion_router",
    "api_create_master_data_router",
    "api_create_operations_router",
    "api_create_reports_router",
    "api_create_snapshot_router",
    "api_create_ui_router",
]
