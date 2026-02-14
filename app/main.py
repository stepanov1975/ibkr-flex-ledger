"""Main module entrypoint for local runtime execution.

This module validates startup configuration and launches the FastAPI service.
"""

import uvicorn

from app.bootstrap import bootstrap_create_application
from app.config import config_load_settings


def main() -> None:
    """Run the API server with validated startup configuration.

    Returns:
        None: This function does not return a runtime value.

    Raises:
        SettingsLoadError: Raised when configuration validation fails.
    """

    settings = config_load_settings()
    application = bootstrap_create_application()
    uvicorn.run(
        application,
        host=settings.application_host,
        port=settings.application_port,
    )


if __name__ == "__main__":
    main()
