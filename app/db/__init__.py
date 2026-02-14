"""Database layer package for all SQL and persistence boundaries."""

from .health import SQLAlchemyDatabaseHealthService
from .ingestion_run import SQLAlchemyIngestionRunService
from .raw_persistence import SQLAlchemyRawPersistenceService
from .interfaces import (
	DatabaseHealthPort,
	IngestionRunAlreadyActiveError,
	IngestionRunRecord,
	IngestionRunReference,
	IngestionRunRepositoryPort,
	IngestionRunState,
	RawArtifactPersistRequest,
	RawArtifactPersistResult,
	RawArtifactRecord,
	RawArtifactReference,
	RawPersistenceRepositoryPort,
	RawRecordPersistRequest,
	RawRecordPersistResult,
)
from .session import db_create_engine, db_create_session_factory

__all__ = [
	"DatabaseHealthPort",
	"IngestionRunRepositoryPort",
	"IngestionRunRecord",
	"IngestionRunReference",
	"IngestionRunState",
	"IngestionRunAlreadyActiveError",
	"RawPersistenceRepositoryPort",
	"RawArtifactReference",
	"RawArtifactPersistRequest",
	"RawArtifactRecord",
	"RawArtifactPersistResult",
	"RawRecordPersistRequest",
	"RawRecordPersistResult",
	"SQLAlchemyDatabaseHealthService",
	"SQLAlchemyIngestionRunService",
	"SQLAlchemyRawPersistenceService",
	"db_create_engine",
	"db_create_session_factory",
]
