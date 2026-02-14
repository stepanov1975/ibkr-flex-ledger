"""Main module entrypoint for local runtime execution.

This module validates startup configuration and launches the FastAPI service.
"""

import argparse

import uvicorn

from app.bootstrap import (
    bootstrap_create_application,
    bootstrap_create_ingestion_orchestrator,
    bootstrap_create_reprocess_orchestrator,
)
from app.config import config_load_settings
from app.db import SQLAlchemyIngestionRunService, db_create_engine
from app.jobs import job_extract_missing_sections_from_diagnostics


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
        choices=("api", "ingestion-run", "reprocess-run"),
        help="Runtime command: `api` starts server, `ingestion-run` triggers one ingestion workflow, "
        "`reprocess-run` triggers one canonical reprocess workflow",
        type=str,
    )
    argument_parser.add_argument(
        "--period-key",
        dest="period_key",
        type=str,
        help="Optional replay period key in YYYY-MM-DD format for `reprocess-run`",
    )
    argument_parser.add_argument(
        "--flex-query-id",
        dest="flex_query_id",
        type=str,
        help="Optional Flex query id override for `reprocess-run`",
    )
    parsed_arguments = argument_parser.parse_args()

    if parsed_arguments.command == "ingestion-run":
        ingestion_orchestrator = bootstrap_create_ingestion_orchestrator()
        execution_result = ingestion_orchestrator.job_execute(job_name="ingestion_run")
        if execution_result.status != "success":
            main_print_latest_missing_sections_diagnostics()
            raise SystemExit(1)
        return

    if parsed_arguments.command == "reprocess-run":
        reprocess_orchestrator = bootstrap_create_reprocess_orchestrator(
            period_key=parsed_arguments.period_key,
            flex_query_id=parsed_arguments.flex_query_id,
        )
        execution_result = reprocess_orchestrator.job_execute(job_name="reprocess_run")
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

def main_print_latest_missing_sections_diagnostics() -> None:
    """Print missing-section diagnostics from the latest ingestion run.

    Returns:
        None: Prints diagnostics to stdout as side effect.

    Raises:
        RuntimeError: This helper does not raise runtime errors.
    """

    settings = config_load_settings()
    engine = db_create_engine(database_url=settings.database_url)
    repository = SQLAlchemyIngestionRunService(engine=engine)
    latest_runs = repository.db_ingestion_run_list(limit=1, offset=0)
    if not latest_runs:
        return

    diagnostics = latest_runs[0].state.diagnostics
    missing_sections_payload = job_extract_missing_sections_from_diagnostics(diagnostics=diagnostics)
    missing_sections = missing_sections_payload["missing_sections"]
    if missing_sections:
        print("MISSING_REQUIRED_SECTION:", ", ".join(missing_sections))


if __name__ == "__main__":
    main()
