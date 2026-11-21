"""Published evidence and snapshot-lineage comparisons for reconciliation."""

from datetime import date
from uuid import UUID

from sqlalchemy import Engine, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from app.domain.flex_parsing import domain_flex_optional_date_matches

from .open_positions import db_open_position_is_summary
from .portfolio_interfaces import ReconciliationSourceRecord
from .published_flex_sections import db_published_flex_sections_history
from .raw_fields import db_raw_numeric


def db_report_reconciliation(
    engine: Engine,
    account_id: str,
    report_date_from: date | None,
    report_date_to: date | None,
    instrument_id: UUID | None,
) -> tuple[list[str], list[ReconciliationSourceRecord]]:
    """Check Published Flex sections and read comparison evidence in one consistent view."""
    required = ("FIFOPerformanceSummaryInBase", "MTMPerformanceSummaryInBase")
    published = db_published_flex_sections_history(account_id, required, include_undated=True).subquery()
    coverage = select(published.c.section_name).distinct()
    if report_date_from is not None:
        coverage = coverage.where(published.c.report_date_local >= report_date_from)
    if report_date_to is not None:
        coverage = coverage.where(published.c.report_date_local <= report_date_to)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            available = set(connection.scalars(coverage))
            missing = [section for section in required if section not in available]
            if missing:
                return missing, []
            return [], db_reconciliation_sources(connection, account_id, report_date_from, report_date_to, instrument_id)
    except SQLAlchemyError as error:
        raise RuntimeError("reconciliation report failed") from error


def db_reconciliation_sources(
    connection: Connection,
    account_id: str,
    report_date_from: date | None,
    report_date_to: date | None,
    instrument_id: UUID | None,
) -> list[ReconciliationSourceRecord]:
    """Compare snapshot lineage with canonical activity on an existing connection."""
    raw_currency = "UPPER(BTRIM(raw.source_payload->>'currency'))"
    raw_fx_rate = db_raw_numeric("fxRateToBase")
    row_fx_rate = (
        f"CASE WHEN {raw_currency}=s.currency THEN 1::numeric ELSE {raw_fx_rate} END"
    )
    def conversion_rate(currency: str) -> str:
        return (
            f"CASE WHEN {currency}=s.currency THEN 1::numeric ELSE "
            "(SELECT fx.fx_rate FROM event_fx fx WHERE fx.account_id=s.account_id "
            f"AND fx.currency={currency} AND fx.functional_currency=s.currency "
            "AND fx.report_date_local<=event.report_date_local AND fx.fx_rate>0 "
            "ORDER BY fx.report_date_local DESC, fx.ingestion_run_id DESC, "
            "fx.source_raw_record_id DESC LIMIT 1) END"
        )

    event_conversion = conversion_rate("event.currency")
    trade_fx_rate = (
        "CASE WHEN event.currency=s.currency THEN 1::numeric ELSE COALESCE("
        "CASE WHEN event.fx_rate_to_base>0 THEN event.fx_rate_to_base END, "
        "CASE WHEN event.net_cash_in_base<>0 THEN ABS(event.net_cash_in_base / NULLIF(event.net_cash, 0)) END, "
        f"({event_conversion})) END"
    )
    commission_currency = (
        "COALESCE(NULLIF(UPPER(BTRIM(raw.source_payload->>'ibCommissionCurrency')), ''), event.currency)"
    )
    commission_fx_rate = (
        f"CASE WHEN {commission_currency}=event.currency THEN ({trade_fx_rate}) "
        f"ELSE ({conversion_rate(commission_currency)}) END"
    )
    position = db_raw_numeric("position")
    unrealized = db_raw_numeric("fifoPnlUnrealized")
    # Canonical identities deduplicate overlapping reports; amounts remain broker-reported.
    trade_history = (
        "FROM event_trade_fill event JOIN raw_record raw ON raw.raw_record_id="
        "COALESCE(event.metadata_source_raw_record_id, event.source_raw_record_id) "
        "JOIN raw_record origin ON origin.raw_record_id=event.source_raw_record_id "
        "WHERE event.account_id=s.account_id AND event.instrument_id=s.instrument_id "
        "AND event.report_date_local<=s.report_date_local "
    )
    cash_history = (
        "FROM event_cashflow event WHERE event.account_id=s.account_id "
        "AND event.instrument_id=s.instrument_id AND event.report_date_local<=s.report_date_local "
    )

    def converted_expense(amount: str, rate: str) -> str:
        # Zero expenses need no FX, but a missing rate for a nonzero expense stays unknown.
        return f"CASE WHEN COALESCE({amount}, 0)=0 THEN 0 ELSE ({amount}) * ({rate}) END"

    def complete_total(value: str, history: str) -> str:
        return (
            "(SELECT CASE WHEN count(*)=0 THEN 0 WHEN bool_and(value IS NOT NULL) THEN sum(value) END "
            f"FROM (SELECT ({value}) AS value {history}) amounts)"
        )

    trade_fees = (
        f"({converted_expense('-event.commission', commission_fx_rate)}) + "
        f"({converted_expense('ABS(event.fees)', trade_fx_rate)})"
    )
    cash_fees = converted_expense("event.fees", event_conversion)
    cash_tax = converted_expense("event.withholding_tax", event_conversion)
    cash_amount = (
        "CASE WHEN event.amount_in_base IS NOT NULL AND event.functional_currency=s.currency "
        f"THEN event.amount_in_base ELSE event.amount * ({event_conversion}) END"
    )
    cash_net = f"({cash_amount}) - ({cash_fees}) - ({cash_tax})"
    cash_reported_fees = f"({cash_fees}) - CASE WHEN event.cash_action='Other Fees' THEN ({cash_amount}) ELSE 0 END"
    cash_reported_tax = (
        f"({cash_tax}) - CASE WHEN event.cash_action='Withholding Tax' THEN ({cash_amount}) ELSE 0 END"
    )
    # A post-settlement commission can revise reported trading P&L and also
    # appear in cash. Use the original trading value only when the linked
    # cash adjustments explain the entire revision in the trade currency.
    original_realized = db_raw_numeric("fifoPnlRealized", "origin")
    adjustment_total = (
        "(SELECT sum(adj.amount) FROM event_cashflow adj JOIN raw_record receipt "
        "ON receipt.raw_record_id=adj.source_raw_record_id WHERE adj.account_id=event.account_id "
        "AND adj.instrument_id=event.instrument_id AND adj.currency=event.currency "
        "AND adj.cash_action='Commission Adjustments' AND receipt.source_payload->>'tradeID'="
        "NULLIF(origin.source_payload->>'tradeID',''))"
    )
    revised_by_adjustment = (
        f"event.realized_pnl<>({original_realized}) AND "
        f"event.realized_pnl-({original_realized})={adjustment_total}"
    )
    realized_value = f"CASE WHEN {revised_by_adjustment} THEN ({original_realized}) ELSE event.realized_pnl END"
    broker_realized = (
        f"{complete_total(f'({realized_value}) * ({trade_fx_rate})', trade_history)} + "
        f"{complete_total(cash_net, cash_history)}"
    )
    broker_fees = f"{complete_total(trade_fees, trade_history)} + {complete_total(cash_reported_fees, cash_history)}"
    broker_tax = complete_total(cash_reported_tax, cash_history)

    # Empty sections have a persisted sentinel; require successful processing of this source lineage.
    complete_positions = (
        "EXISTS(SELECT 1 FROM raw_record section "
        "JOIN ingestion_run run ON run.ingestion_run_id=section.ingestion_run_id "
        "LEFT JOIN raw_artifact artifact ON artifact.raw_artifact_id=section.raw_artifact_id "
        "LEFT JOIN ingestion_run completion ON completion.ingestion_run_id=artifact.completed_ingestion_run_id "
        "WHERE section.account_id=s.account_id AND section.ingestion_run_id=s.ingestion_run_id "
        "AND section.report_date_local=s.report_date_local AND section.section_name='OpenPositions' "
        "AND (completion.status='success' "
        "OR (artifact.completed_ingestion_run_id IS NULL AND run.status='success')))"
    )
    absent_position = (
        "NOT EXISTS(SELECT 1 FROM raw_record present WHERE present.account_id=s.account_id "
        "AND present.ingestion_run_id=s.ingestion_run_id AND present.report_date_local=s.report_date_local "
        "AND present.section_name='OpenPositions' AND present.source_payload->>'conid'=i.conid)"
    )
    closed_position = ("CASE WHEN UPPER(BTRIM(i.asset_category)) NOT IN ('CASH','FX') "
                       f"AND ({complete_positions}) AND ({absent_position}) THEN 0::numeric END")

    # Keep the canonical event and the raw version used by its calculation together.
    history_sources = (
        "(SELECT jsonb_agg(to_jsonb(src) ORDER BY src.day DESC, src.event_id) FROM ("
        "SELECT event.event_trade_fill_id AS event_id, raw.raw_record_id, event.report_date_local AS day, "
        f"CASE WHEN {revised_by_adjustment} THEN origin.raw_record_id "
        f"WHEN round(({db_raw_numeric('fifoPnlRealized')}),8) IS NOT DISTINCT FROM event.realized_pnl "
        "THEN raw.raw_record_id END AS realized_raw_id, "
        "(COALESCE(event.commission,0)<>0 OR COALESCE(event.fees,0)<>0) "
        f"AND COALESCE(round(({db_raw_numeric('ibCommission')}),8),0)=COALESCE(event.commission,0) "
        f"AND COALESCE(round(({db_raw_numeric('fees')}),8),0)=COALESCE(event.fees,0) AS has_fee, false AS has_tax "
        f"{trade_history} UNION ALL "
        "SELECT event.event_cashflow_id, event.source_raw_record_id, event.report_date_local, event.source_raw_record_id, "
        "COALESCE(event.fees,0)<>0 OR event.cash_action='Other Fees', "
        "COALESCE(event.withholding_tax,0)<>0 OR event.cash_action='Withholding Tax' "
        f"{cash_history}) src)"
    )

    query = text(
        "WITH snapshots AS (SELECT * FROM pnl_snapshot_daily WHERE account_id=:account_id "
        "AND (CAST(:date_from AS date) IS NULL OR report_date_local>=:date_from) "
        "AND (CAST(:date_to AS date) IS NULL OR report_date_local<=:date_to) "
        "AND (CAST(:instrument_id AS uuid) IS NULL OR instrument_id=CAST(:instrument_id AS uuid))), "
        "position_dates AS (SELECT dated.ingestion_run_id, dated.report_date_local, "
        "array_agg(DISTINCT dated.source_payload->>'reportDate') AS dates FROM raw_record dated "
        "JOIN (SELECT DISTINCT ingestion_run_id, report_date_local FROM snapshots) source "
        "USING (ingestion_run_id, report_date_local) WHERE dated.account_id=:account_id "
        "AND dated.section_name='OpenPositions' AND dated.source_row_ref LIKE 'OpenPositions:OpenPosition:%' "
        "GROUP BY dated.ingestion_run_id, dated.report_date_local) "
        "SELECT s.report_date_local, s.instrument_id, i.conid, i.symbol, s.currency, s.position_qty, "
        "s.realized_pnl, s.unrealized_pnl, s.fees, s.withholding_tax, s.provisional, "
        f"COALESCE({position}, {closed_position}) AS broker_position_qty, "
        f"({broker_realized}) AS broker_realized_pnl, "
        f"COALESCE(({unrealized}) * ({row_fx_rate}), {closed_position}) AS broker_unrealized_pnl, "
        f"({broker_fees}) AS broker_fees, "
        f"{broker_tax} AS broker_withholding_tax, "
        f"i.asset_category, raw.raw_record_id AS position_raw_id, {history_sources} AS history_sources, "
        "position_dates.dates AS position_report_dates "
        "FROM snapshots s JOIN instrument i USING (instrument_id) "
        "LEFT JOIN position_dates USING (ingestion_run_id, report_date_local) "
        "LEFT JOIN LATERAL (SELECT raw.raw_record_id, raw.source_payload, count(*) OVER () AS summary_count FROM raw_record raw "
        "WHERE raw.account_id=s.account_id AND raw.ingestion_run_id=s.ingestion_run_id "
        "AND raw.section_name='OpenPositions' AND raw.report_date_local=s.report_date_local "
        f"AND raw.source_payload->>'conid'=i.conid AND {db_open_position_is_summary('raw')}) raw ON raw.summary_count=1 "
        "ORDER BY s.report_date_local asc, i.symbol asc, s.instrument_id asc"
    )
    rows = connection.execute(query, {
        "account_id": account_id, "date_from": report_date_from, "date_to": report_date_to,
        "instrument_id": instrument_id,
    }).mappings().all()
    result = []
    for row in rows:
        position_dates_match = all(
            domain_flex_optional_date_matches(day, row["report_date_local"]) for day in row["position_report_dates"] or []
        )
        position_sources: list[tuple[UUID | None, UUID]] = (
            [(None, row["position_raw_id"])] if position_dates_match and row["position_raw_id"] else []
        )
        sources: dict[str, list[tuple[UUID | None, UUID]]] = {
            "position_qty": position_sources, "unrealized_pnl": position_sources,
            "realized_pnl": [], "fees": [], "withholding_tax": [],
        }
        for entry in row["history_sources"] or []:
            source = (UUID(entry["event_id"]), UUID(entry["raw_record_id"]))
            if entry["realized_raw_id"]:
                sources["realized_pnl"].append((source[0], UUID(entry["realized_raw_id"])))
            if entry["has_fee"]:
                sources["fees"].append(source)
            if entry["has_tax"]:
                sources["withholding_tax"].append(source)
        result.append(ReconciliationSourceRecord(
            report_date_local=row["report_date_local"], instrument_id=row["instrument_id"], conid=row["conid"],
            symbol=row["symbol"], currency=row["currency"], position_qty=str(row["position_qty"]),
            realized_pnl=str(row["realized_pnl"]), unrealized_pnl=str(row["unrealized_pnl"]), fees=str(row["fees"]),
            withholding_tax=str(row["withholding_tax"]),
            broker_position_qty=_numeric(row["broker_position_qty"]) if position_dates_match else None,
            broker_realized_pnl=_numeric(row["broker_realized_pnl"]),
            broker_unrealized_pnl=_numeric(row["broker_unrealized_pnl"]) if position_dates_match else None,
            broker_fees=_numeric(row["broker_fees"]),
            broker_withholding_tax=_numeric(row["broker_withholding_tax"]),
            asset_category=row["asset_category"], sources_by_metric=sources,
            provisional=bool(row["provisional"]) or not position_dates_match,
        ))
    return result

def _numeric(value: object | None) -> str | None:
    return None if value is None else str(value)
