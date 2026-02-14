"""Typed runtime settings with dotenv support and startup validation."""

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsLoadError(RuntimeError):
    """Raised when runtime settings cannot be loaded or validated."""


class AppSettings(BaseSettings):
    """Application settings for API runtime and ingestion configuration.

    Environment variable names map directly to field names in uppercase.
    Example: `database_url` reads from `DATABASE_URL`.

    Attributes:
        environment_name: Runtime environment label.
        application_host: Host interface for web server binding.
        application_port: Web server port.
        database_url: PostgreSQL DSN for application database access.
        ibkr_flex_token: Flex Web Service token.
        ibkr_flex_query_id: Flex query identifier.
        api_default_limit: Default list endpoint limit.
        api_max_limit: Maximum allowed list endpoint limit.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment_name: str = Field(default="development")
    application_host: str = Field(default="0.0.0.0")
    application_port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = Field(default="postgresql+psycopg://postgres:postgres@localhost:5432/postgres")
    ibkr_flex_token: str = Field(min_length=1)
    ibkr_flex_query_id: str = Field(min_length=1)
    api_default_limit: int = Field(default=50, ge=1)
    api_max_limit: int = Field(default=200, ge=1)

    @field_validator("ibkr_flex_token", "ibkr_flex_query_id")
    @classmethod
    def _validate_non_empty_string(cls, value: str) -> str:
        stripped_value = value.strip()
        if not stripped_value:
            raise ValueError("value must not be blank")
        return stripped_value

    @field_validator("api_max_limit")
    @classmethod
    def _validate_limit_bounds(cls, value: int, info) -> int:
        default_limit = info.data.get("api_default_limit", 50)
        if value < default_limit:
            raise ValueError("api_max_limit must be greater than or equal to api_default_limit")
        return value


class DatabaseUrlSettings(BaseSettings):
    """Minimal settings model used by migration tooling.

    This model intentionally validates only database connectivity inputs so
    schema migration commands can run without requiring full runtime
    application settings.

    Attributes:
        database_url: PostgreSQL DSN for schema migrations.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(default="postgresql+psycopg://postgres:postgres@localhost:5432/postgres")


def config_load_settings() -> AppSettings:
    """Load and validate runtime settings from environment and dotenv.

    Returns:
        AppSettings: Validated runtime settings object.

    Raises:
        SettingsLoadError: Raised when required settings are missing or invalid.
    """

    try:
        return AppSettings()
    except ValidationError as error:
        raise SettingsLoadError(
            f"Startup configuration validation failed. Update .env or environment variables. Details: {error}"
        ) from error


def config_load_database_url() -> str:
    """Load and validate only the database URL setting.

    Returns:
        str: Non-empty database URL for migration and db tooling.

    Raises:
        SettingsLoadError: Raised when database URL cannot be loaded.
    """

    try:
        database_settings = DatabaseUrlSettings()
    except ValidationError as error:
        raise SettingsLoadError(
            f"Database URL configuration validation failed. Update .env or environment variables. Details: {error}"
        ) from error

    database_url = str(database_settings.database_url).strip()
    if not database_url:
        raise SettingsLoadError("Database URL configuration validation failed. DATABASE_URL must not be blank.")
    return database_url
