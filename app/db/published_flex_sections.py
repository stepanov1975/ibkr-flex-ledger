"""Composable source selection for current and historical published Flex sections."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Date, DateTime, String, Uuid, and_, bindparam, column, or_, select, table
from sqlalchemy.sql import Select


def db_published_flex_sections_latest(account_id: str, sections: Sequence[str]) -> Select[Any]:
    """Select one dated published source per requested section, before filtering its rows.

    Explicit empty sections supersede older sources; omitted sections do not.
    Statement date, artifact creation time and artifact identity determine the
    newest version, independently for each section.

    Args:
        account_id: Internal account whose published evidence is requested.
        sections: Whole section names to select; unknown or empty names yield no sources.

    Returns:
        A relation with section_name, raw_artifact_id, report_date_local and
        created_at_utc. Callers execute it in their existing connection.
    """
    history = db_published_flex_sections_history(account_id, sections)
    sources = history.selected_columns
    return history.distinct(sources.section_name).order_by(
        sources.section_name, sources.report_date_local.desc(),
        sources.created_at_utc.desc(), sources.raw_artifact_id.desc(),
    )


def db_published_flex_sections_history(
    account_id: str, sections: Sequence[str], *, include_undated: bool = False,
) -> Select[Any]:
    """Select every published source containing a requested section, without event deduplication.

    Completion status controls publication when recorded; otherwise a successful
    original import is eligible. Section markers establish presence even when
    no business rows remain after a caller's filters.

    Args:
        account_id: Internal account whose published evidence is requested.
        sections: Whole section names to select.
        include_undated: Include undated historical sources for transaction-tax reporting.

    Returns:
        An unordered relation with section_name, raw_artifact_id,
        report_date_local and created_at_utc, once per section and artifact.
        It performs no database I/O and owns no connection or transaction.
    """
    artifact = table(
        "raw_artifact", column("raw_artifact_id", Uuid()), column("account_id", String()),
        column("ingestion_run_id", Uuid()), column("completed_ingestion_run_id", Uuid()),
        column("report_date_local", Date()), column("created_at_utc", DateTime(timezone=True)),
    ).alias("artifact")
    raw = table("raw_record", column("raw_artifact_id", Uuid()), column("section_name", String()))
    runs = table("ingestion_run", column("ingestion_run_id", Uuid()), column("status", String()))
    owner, completion = runs.alias("owner"), runs.alias("completion")
    history = select(
        raw.c.section_name, artifact.c.raw_artifact_id,
        artifact.c.report_date_local, artifact.c.created_at_utc,
    ).select_from(
        artifact.join(owner, owner.c.ingestion_run_id == artifact.c.ingestion_run_id)
        .outerjoin(completion, completion.c.ingestion_run_id == artifact.c.completed_ingestion_run_id)
        .join(raw, raw.c.raw_artifact_id == artifact.c.raw_artifact_id)
    ).where(
        artifact.c.account_id == bindparam("account_id", account_id),
        raw.c.section_name.in_(sections),
        or_(completion.c.status == "success", and_(
            artifact.c.completed_ingestion_run_id.is_(None), owner.c.status == "success",
        )),
    ).distinct()
    return history if include_undated else history.where(artifact.c.report_date_local.is_not(None))
