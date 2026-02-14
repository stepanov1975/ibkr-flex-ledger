"""Main module entrypoint for local runtime execution.

This module validates startup configuration and launches the FastAPI service.
"""

import argparse

import uvicorn

from app.bootstrap import bootstrap_create_application, bootstrap_create_ingestion_orchestrator
from app.config import config_load_settings


def main() -> None:
    """Run selected runtime command with validated startup configuration.

    Returns:
        None: This function does not return a runtime value.

    Raises:
        SettingsLoadError: Raised when configuration validation fails.
    """

    argument_parser = argparse.ArgumentParser(description="IBKR Flex Ledger runtime entrypoint")
    argument_parser.add_argument(
        "command",
        nargs="?",
        default="api",
        choices=("api", "ingestion-run"),
        help="Runtime command: `api` starts server, `ingestion-run` triggers one ingestion workflow",
        type=str,
    )
    parsed_arguments = argument_parser.parse_args()

    if parsed_arguments.command == "ingestion-run":
        ingestion_orchestrator = bootstrap_create_ingestion_orchestrator()
        execution_result = ingestion_orchestrator.job_execute(job_name="ingestion_run")
        if execution_result.status != "success":
            raise SystemExit(1)
        return

    settings = config_load_settings()
    application = bootstrap_create_application()
    uvicorn.run(
        application,
        host=settings.application_host,
        port=settings.application_port,
    )


if __name__ == "__main__":
    main()
