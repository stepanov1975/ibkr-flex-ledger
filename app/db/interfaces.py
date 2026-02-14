"""Typed interfaces for database-layer services.

All SQL and ORM access must remain in the db package and its submodules.
"""

from typing import Protocol

from app.domain import HealthStatus


class DatabaseHealthPort(Protocol):
    """Port definition for database connectivity verification."""

    def db_connection_label(self) -> str:
        """Return a stable label for the active database connection target.

        Returns:
            str: Database target label for diagnostics.

        Raises:
            RuntimeError: Raised when connection metadata is unavailable.
        """

    def db_check_health(self) -> HealthStatus:
        """Check database connectivity and return deterministic health payload.

        Returns:
            HealthStatus: Database health status payload.

        Raises:
            ConnectionError: Raised when database cannot be reached.
        """
