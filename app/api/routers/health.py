"""Health endpoint router composition for app and database checks."""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.db import DatabaseHealthPort


def api_create_health_router(db_health_service: DatabaseHealthPort) -> APIRouter:
    """Create health-check router with app and database connectivity status.

    Args:
        db_health_service: DB-layer health service interface.

    Returns:
        APIRouter: Router exposing `/health` endpoint.

    Raises:
        ValueError: Raised when db_health_service is invalid.
    """

    if db_health_service is None:
        raise ValueError("db_health_service must not be None")

    router = APIRouter(tags=["health"])

    @router.get("/health")
    def api_health_status() -> JSONResponse:
        """Return application and database health state.

        Returns:
            JSONResponse: Deterministic health payload for operational checks.

        Raises:
            ConnectionError: Raised when database health check fails.
        """

        try:
            db_health = db_health_service.db_check_health()
            payload = {
                "status": "ok",
                "app": "up",
                "database": db_health.status,
                "detail": db_health.detail,
                "target": db_health_service.db_connection_label(),
            }
            return JSONResponse(content=payload, status_code=status.HTTP_200_OK)
        except ConnectionError as error:
            payload = {
                "status": "degraded",
                "app": "up",
                "database": "down",
                "detail": str(error),
                "target": db_health_service.db_connection_label(),
            }
            return JSONResponse(content=payload, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    return router
