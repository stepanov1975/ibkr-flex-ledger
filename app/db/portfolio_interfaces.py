"""Typed database contracts for portfolio workflows and reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class InstrumentRecord:
    instrument_id: UUID
    conid: str
    symbol: str
    currency: str
    asset_category: str
    description: str | None
    active: bool
    labels: tuple[dict[str, str | None], ...]
    updated_at_utc: datetime


@dataclass(frozen=True)
class LabelRecord:
    label_id: UUID
    name: str
    color: str | None
    created_at_utc: datetime
    updated_at_utc: datetime


@dataclass(frozen=True)
class NoteRecord:
    note_id: UUID
    instrument_id: UUID | None
    label_id: UUID | None
    content: str
    created_at_utc: datetime
    updated_at_utc: datetime


@dataclass(frozen=True)
class CorporateActionManualCaseRecord:
    case_id: UUID
    event_corp_action_id: UUID
    action_type: str
    instrument_id: UUID
    symbol: str
    status: str
    owner: str | None
    resolution_note: str | None
    resolved_at_utc: datetime | None
    created_at_utc: datetime
    updated_at_utc: datetime
    report_date_local: date
    description: str | None
    requires_manual: bool


@dataclass(frozen=True)
class InstrumentPnlReportRecord:
    report_date_local: date
    instrument_id: UUID
    conid: str
    symbol: str
    currency: str
    position_qty: str
    cost_basis: str | None
    realized_pnl: str
    unrealized_pnl: str
    total_pnl: str
    provisional: bool
    unresolved_case_count: int


@dataclass(frozen=True)
class LabelPnlReportRecord:
    report_date_local: date
    label_id: UUID
    label_name: str
    instrument_count: int
    realized_pnl: str
    unrealized_pnl: str
    total_pnl: str
    fees: str
    withholding_tax: str
    provisional: bool


@dataclass(frozen=True)
class CashBalanceReportRecord:
    currency: str
    amount: str | None


@dataclass(frozen=True)
class TransferSummaryReportRecord:
    currency: str
    net_transfers: str
    gross_deposits: str
    gross_withdrawals: str


@dataclass(frozen=True)
class TransferReportRecord:
    report_date_local: date
    transfer_type: str
    amount: str
    currency: str
    description: str | None


@dataclass(frozen=True)
class CostSummaryReportRecord:
    category: str
    net_cost_usd: str | None
    included_in_instrument_pnl: bool


@dataclass(frozen=True)
class SecuritiesCommissionSummaryReportRecord:
    instrument_type: str
    side: str
    execution_count: int
    instrument_count: int
    commission_usd: str | None


@dataclass(frozen=True)
class PortfolioSummaryReportRecord:
    report_date_local: date | None
    cash_balances: tuple[CashBalanceReportRecord, ...]
    transfer_summary_by_currency: tuple[TransferSummaryReportRecord, ...]
    transfers: tuple[TransferReportRecord, ...]
    activity_date_from: date | None
    activity_date_to: date | None
    cost_summary: tuple[CostSummaryReportRecord, ...]
    total_costs_usd: str | None
    costs_outside_instrument_pnl_usd: str | None
    gross_dividend_payments_usd: str | None
    dividend_withholding_tax_usd: str | None
    net_dividend_payments_usd: str | None
    securities_commission_summary: tuple[SecuritiesCommissionSummaryReportRecord, ...]
    securities_commission_date_from: date | None
    securities_commission_date_to: date | None
    securities_commission_execution_count: int
    securities_commission_instrument_count: int
    securities_commission_total_usd: str | None
    net_transfers_usd: str | None
    estimated_net_liquidation_value_usd: str | None
    total_profit_usd: str | None
    profit_percent: str | None


@dataclass(frozen=True)
class ProvenanceRecord:
    event_type: str
    event_id: UUID
    source_raw_record_id: UUID
    section_name: str
    source_row_ref: str
    source_payload: dict[str, object]
    raw_artifact_id: UUID | None = None
    ingestion_run_id: UUID | None = None


@dataclass(frozen=True)
class ReconciliationSourceRecord:
    report_date_local: date
    instrument_id: UUID
    conid: str
    symbol: str
    currency: str
    position_qty: str
    realized_pnl: str
    unrealized_pnl: str
    fees: str
    withholding_tax: str
    broker_position_qty: str | None
    broker_realized_pnl: str | None
    broker_unrealized_pnl: str | None
    broker_fees: str | None
    broker_withholding_tax: str | None
    source_event_id: UUID | None
    source_raw_record_id: UUID | None
    provisional: bool


@dataclass(frozen=True)
class IngestionSloRecord:
    status: str
    started_at_utc: datetime
    ended_at_utc: datetime | None
    duration_ms: int | None


@dataclass(frozen=True)
class DiagnosticArchiveRecord:
    ingestion_run_id: UUID
    account_id: str
    run_type: str
    status: str
    started_at_utc: datetime
    diagnostics: list[dict[str, object]]


class PortfolioRepositoryPort(Protocol):
    def db_instrument_list(
        self,
        account_id: str,
        limit: int,
        offset: int,
        sort_by: str,
        sort_dir: str,
        label_id: UUID | None,
        search: str | None,
        active_only: bool,
    ) -> tuple[list[InstrumentRecord], int]: ...

    def db_instrument_get(self, account_id: str, instrument_id: UUID) -> InstrumentRecord | None: ...

    def db_label_list(self) -> list[LabelRecord]: ...

    def db_label_create(self, name: str, color: str | None) -> LabelRecord: ...

    def db_label_update(
        self, label_id: UUID, name: str | None, color: str | None, *, update_color: bool = False
    ) -> LabelRecord | None: ...

    def db_label_delete(self, label_id: UUID) -> bool: ...

    def db_instrument_label_assign(self, instrument_id: UUID, label_id: UUID) -> bool: ...

    def db_instrument_label_remove(self, instrument_id: UUID, label_id: UUID) -> bool: ...

    def db_note_create(self, instrument_id: UUID | None, label_id: UUID | None, content: str) -> NoteRecord: ...

    def db_note_update(self, note_id: UUID, content: str) -> NoteRecord | None: ...

    def db_note_delete(self, note_id: UUID) -> bool: ...

    def db_note_list(
        self,
        limit: int,
        offset: int,
        sort_by: str,
        sort_dir: str,
        instrument_id: UUID | None,
        label_id: UUID | None,
        created_from: datetime | None,
        created_to: datetime | None,
    ) -> tuple[list[NoteRecord], int]: ...

    def db_manual_case_list(self, status: str | None) -> list[CorporateActionManualCaseRecord]: ...

    def db_manual_case_update(
        self,
        case_id: UUID,
        status: str,
        owner: str | None,
        resolution_note: str | None,
    ) -> CorporateActionManualCaseRecord | None: ...

    def db_report_pnl_by_instrument(
        self,
        account_id: str,
        report_date_from: date | None,
        report_date_to: date | None,
        instrument_id: UUID | None,
    ) -> list[InstrumentPnlReportRecord]: ...

    def db_report_pnl_by_label(
        self,
        account_id: str,
        report_date_from: date | None,
        report_date_to: date | None,
        label_id: UUID | None,
    ) -> list[LabelPnlReportRecord]: ...

    def db_report_portfolio_summary(self, account_id: str) -> PortfolioSummaryReportRecord: ...

    def db_report_provenance(
        self,
        account_id: str,
        report_date_local: date,
        instrument_id: UUID,
    ) -> list[ProvenanceRecord]: ...

    def db_reconciliation_sources(
        self,
        account_id: str,
        report_date_from: date | None,
        report_date_to: date | None,
        instrument_id: UUID | None,
    ) -> list[ReconciliationSourceRecord]: ...

    def db_reconciliation_missing_sections(
        self,
        account_id: str,
        report_date_from: date | None,
        report_date_to: date | None,
    ) -> list[str]: ...

    def db_ingestion_slo_records(self, account_id: str, since_utc: datetime) -> list[IngestionSloRecord]: ...

    def db_diagnostics_archive_candidates(self, cutoff_utc: datetime) -> list[DiagnosticArchiveRecord]: ...

    def db_diagnostics_purge(self, ingestion_run_ids: list[UUID]) -> int: ...
