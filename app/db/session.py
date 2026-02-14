"""Database engine and session factory utilities.

This module centralizes database connectivity primitives to enforce the db-layer
boundary for all SQLAlchemy usage.
"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker


def db_create_engine(database_url: str) -> Engine:
    """Create the SQLAlchemy engine for application database access.

    Args:
        database_url: SQLAlchemy database URL.

    Returns:
        Engine: Configured SQLAlchemy engine.

    Raises:
        ValueError: Raised when the database URL is blank.
    """

    if not database_url.strip():
        raise ValueError("database_url must not be blank")

    return create_engine(database_url, pool_pre_ping=True)


def db_create_session_factory(engine: Engine) -> sessionmaker:
    """Create a SQLAlchemy session factory bound to the given engine.

    Args:
        engine: SQLAlchemy engine instance.

    Returns:
        sessionmaker: Session factory for db-layer repositories.

    Raises:
        ValueError: Raised when engine is invalid.
    """

    if engine is None:
        raise ValueError("engine must not be None")

    return sessionmaker(bind=engine, autoflush=False, autocommit=False)
