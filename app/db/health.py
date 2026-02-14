"""Database health service implementations for connectivity checks."""

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.domain import HealthStatus

from .interfaces import DatabaseHealthPort


class SQLAlchemyDatabaseHealthService(DatabaseHealthPort):
    """Database health service backed by SQLAlchemy engine connectivity checks."""

    def __init__(self, engine: Engine):
        """Initialize database health service.

        Args:
            engine: SQLAlchemy engine used for connectivity checks.

        Raises:
            ValueError: Raised when engine is None.
        """

        if engine is None:
            raise ValueError("engine must not be None")
        self._engine = engine

    def db_connection_label(self) -> str:
        """Return the target database URL for diagnostics.

        Returns:
            str: Rendered engine URL string.

        Raises:
            RuntimeError: Raised if URL rendering fails.
        """

        return self._engine.url.render_as_string(hide_password=True)

    def db_check_health(self) -> HealthStatus:
        """Verify database connectivity using a deterministic lightweight query.

        Returns:
            HealthStatus: Health payload with status and diagnostic detail.

        Raises:
            ConnectionError: Raised when connectivity check fails.
        """

        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return HealthStatus(status="ok", detail="database connectivity verified")
        except SQLAlchemyError as error:
            raise ConnectionError("database connectivity check failed") from error
