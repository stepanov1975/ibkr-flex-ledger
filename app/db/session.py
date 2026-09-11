"""Database engine and session factory utilities.

This module centralizes database connectivity primitives to enforce the db-layer
boundary for all SQLAlchemy usage.
"""

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import ContextManager, Iterator

from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


_active_transaction: ContextVar[tuple[Engine, Connection] | None] = ContextVar(
    "db_active_transaction", default=None,
)
_workflow_connection: ContextVar[tuple[Engine, Connection] | None] = ContextVar(
    "db_workflow_connection", default=None,
)


@contextmanager
def db_workflow_connection_scope(engine: Engine, connection: Connection) -> Iterator[None]:
    """Use the workflow's lock-owning session for its publication transaction."""
    token = _workflow_connection.set((engine, connection))
    try:
        yield
    finally:
        _workflow_connection.reset(token)


def db_connection_scope(engine: Engine, write: bool = False) -> ContextManager[Connection]:
    """Join this execution's transaction, or open an independent connection."""
    active = _active_transaction.get()
    if active is not None and active[0] is engine:
        return nullcontext(active[1])
    return engine.begin() if write else engine.connect()


@contextmanager
def db_transaction_scope(engine: Engine) -> Iterator[None]:
    """Publish related repository writes together without sharing across workers."""
    active = _active_transaction.get()
    if active is not None and active[0] is engine:
        yield
        return
    owned = _workflow_connection.get()
    scope = nullcontext(owned[1]) if owned is not None and owned[0] is engine else engine.connect()
    with scope as connection:
        if connection.invalidated or connection.closed:
            raise RuntimeError("workflow database session lost; publication refused")
        with connection.begin():
            token = _active_transaction.set((engine, connection))
            try:
                yield
            finally:
                _active_transaction.reset(token)


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

    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def db_create_session_factory(engine: Engine) -> sessionmaker[Session]:
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
