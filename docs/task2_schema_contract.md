# Task 2 Schema Contract (MVP Baseline)

Date: 2026-02-14
Status: Implementation contract for migration authoring
Scope: Task 2 from implementation_task_list.md

## Purpose

This contract defines the full column-level PostgreSQL schema for all MVP tables required in Task 2.
It is the source document used to author the baseline migration and avoid schema drift.

Fixed decisions used:
- Full column-level schema is implemented in Task 2.
- UUID primary keys are database-generated with `gen_random_uuid()`.
- Canonical natural keys and constraint names follow `MVP_spec_freeze.md`.

## Global DB Rules

- Enable extension `pgcrypto` for `gen_random_uuid()` defaults.
- All primary keys are `uuid` with `DEFAULT gen_random_uuid()` unless stated otherwise.
- All timestamps are stored in UTC using `timestamptz`.
- All money and quantity-like values use `numeric(24,8)` unless otherwise stated.
- `account_id` remains internal-only but is stored for deterministic natural keys.

## Tables

### 1) instrument
- `instrument_id uuid primary key default gen_random_uuid()`
- `account_id text not null`
- `conid text not null`
- `symbol text not null`
- `local_symbol text null`
- `isin text null`
- `cusip text null`
- `figi text null`
- `asset_category text not null`
- `currency text not null`
- `description text null`
- `active boolean not null default true`
- `created_at_utc timestamptz not null default now()`
- `updated_at_utc timestamptz not null default now()`

Constraints and indexes:
- `uq_instrument_account_conid` unique (`account_id`, `conid`)
- index on (`symbol`)
- index on (`updated_at_utc`)

### 2) label
- `label_id uuid primary key default gen_random_uuid()`
- `name text not null`
- `color text null`
- `created_at_utc timestamptz not null default now()`
- `updated_at_utc timestamptz not null default now()`

Constraints and indexes:
- `uq_label_name` unique (`name`)

### 3) instrument_label
- `instrument_label_id uuid primary key default gen_random_uuid()`
- `instrument_id uuid not null references instrument(instrument_id) on delete cascade`
- `label_id uuid not null references label(label_id) on delete cascade`
- `created_at_utc timestamptz not null default now()`

Constraints and indexes:
- `uq_instrument_label_pair` unique (`instrument_id`, `label_id`)
- index on (`label_id`, `instrument_id`)

### 4) note
- `note_id uuid primary key default gen_random_uuid()`
- `instrument_id uuid null references instrument(instrument_id) on delete set null`
- `label_id uuid null references label(label_id) on delete set null`
- `content text not null`
- `created_at_utc timestamptz not null default now()`
- `updated_at_utc timestamptz not null default now()`

Constraints and indexes:
- check: at least one of `instrument_id` or `label_id` is not null
- index on (`created_at_utc`)
- index on (`instrument_id`, `created_at_utc`)
- index on (`label_id`, `created_at_utc`)

### 5) ingestion_run
- `ingestion_run_id uuid primary key default gen_random_uuid()`
- `account_id text not null`
- `run_type text not null default 'scheduled'`
- `status text not null`
- `period_key text not null`
- `flex_query_id text not null`
- `report_date_local date null`
- `started_at_utc timestamptz not null`
- `ended_at_utc timestamptz null`
- `duration_ms bigint null`
- `error_code text null`
- `error_message text null`
- `diagnostics jsonb null`
- `created_at_utc timestamptz not null default now()`

Constraints and indexes:
- check `status` in (`started`, `success`, `failed`)
- check `run_type` in (`scheduled`, `manual`, `reprocess`)
- index on (`started_at_utc` desc, `ingestion_run_id` desc)
- index on (`status`, `started_at_utc` desc)

### 6) raw_record
- `raw_record_id uuid primary key default gen_random_uuid()`
- `ingestion_run_id uuid not null references ingestion_run(ingestion_run_id) on delete cascade`
- `account_id text not null`
- `period_key text not null`
- `flex_query_id text not null`
- `payload_sha256 text not null`
- `report_date_local date null`
- `section_name text not null`
- `source_row_ref text not null`
- `source_payload jsonb not null`
- `created_at_utc timestamptz not null default now()`

Constraints and indexes:
- `uq_raw_record_section_source_ref` unique (`ingestion_run_id`, `section_name`, `source_row_ref`)
- index on (`period_key`, `flex_query_id`, `payload_sha256`)
- index on (`section_name`)
- index on (`created_at_utc`)

### 7) event_trade_fill
- `event_trade_fill_id uuid primary key default gen_random_uuid()`
- `account_id text not null`
- `instrument_id uuid not null references instrument(instrument_id)`
- `ingestion_run_id uuid not null references ingestion_run(ingestion_run_id)`
- `source_raw_record_id uuid not null references raw_record(raw_record_id)`
- `ib_exec_id text not null`
- `transaction_id text null`
- `trade_timestamp_utc timestamptz not null`
- `report_date_local date not null`
- `side text not null`
- `quantity numeric(24,8) not null`
- `price numeric(24,8) not null`
- `cost numeric(24,8) null`
- `commission numeric(24,8) null`
- `fees numeric(24,8) null`
- `realized_pnl numeric(24,8) null`
- `net_cash numeric(24,8) null`
- `net_cash_in_base numeric(24,8) null`
- `fx_rate_to_base numeric(24,10) null`
- `currency text not null`
- `functional_currency text not null default 'USD'`
- `created_at_utc timestamptz not null default now()`

Constraints and indexes:
- check `side` in (`BUY`, `SELL`)
- natural-key unique constraint `uq_event_trade_fill_account_exec` unique (`account_id`, `ib_exec_id`)
- index on (`instrument_id`, `report_date_local`)
- index on (`ingestion_run_id`)
- index on (`source_raw_record_id`)

### 8) event_cashflow
- `event_cashflow_id uuid primary key default gen_random_uuid()`
- `account_id text not null`
- `instrument_id uuid null references instrument(instrument_id)`
- `ingestion_run_id uuid not null references ingestion_run(ingestion_run_id)`
- `source_raw_record_id uuid not null references raw_record(raw_record_id)`
- `transaction_id text not null`
- `cash_action text not null`
- `report_date_local date not null`
- `effective_at_utc timestamptz null`
- `amount numeric(24,8) not null`
- `amount_in_base numeric(24,8) null`
- `currency text not null`
- `functional_currency text not null default 'USD'`
- `withholding_tax numeric(24,8) null`
- `fees numeric(24,8) null`
- `is_correction boolean not null default false`
- `created_at_utc timestamptz not null default now()`

Constraints and indexes:
- natural-key unique constraint `uq_event_cashflow_account_txn_action_ccy` unique (`account_id`, `transaction_id`, `cash_action`, `currency`)
- index on (`instrument_id`, `report_date_local`)
- index on (`ingestion_run_id`)
- index on (`source_raw_record_id`)

### 9) event_fx
- `event_fx_id uuid primary key default gen_random_uuid()`
- `account_id text not null`
- `ingestion_run_id uuid not null references ingestion_run(ingestion_run_id)`
- `source_raw_record_id uuid not null references raw_record(raw_record_id)`
- `transaction_id text not null`
- `report_date_local date not null`
- `currency text not null`
- `functional_currency text not null default 'USD'`
- `fx_rate numeric(24,10) null`
- `fx_source text not null`
- `provisional boolean not null default false`
- `diagnostic_code text null`
- `created_at_utc timestamptz not null default now()`

Constraints and indexes:
- natural-key unique constraint `uq_event_fx_account_txn_ccy_pair` unique (`account_id`, `transaction_id`, `currency`, `functional_currency`)
- index on (`report_date_local`)
- index on (`ingestion_run_id`)
- index on (`source_raw_record_id`)

### 10) event_corp_action
- `event_corp_action_id uuid primary key default gen_random_uuid()`
- `account_id text not null`
- `instrument_id uuid null references instrument(instrument_id)`
- `conid text not null`
- `ingestion_run_id uuid not null references ingestion_run(ingestion_run_id)`
- `source_raw_record_id uuid not null references raw_record(raw_record_id)`
- `action_id text null`
- `transaction_id text null`
- `reorg_code text not null`
- `report_date_local date not null`
- `description text null`
- `requires_manual boolean not null default false`
- `provisional boolean not null default false`
- `manual_case_id uuid null`
- `created_at_utc timestamptz not null default now()`

Constraints and indexes:
- natural-key unique constraint `uq_event_corp_action_account_action` unique (`account_id`, `action_id`)
- fallback natural-key unique constraint `uq_event_corp_action_fallback` unique (`account_id`, `transaction_id`, `conid`, `report_date_local`, `reorg_code`)
- check: at least one of `action_id` or `transaction_id` is not null
- index on (`instrument_id`, `report_date_local`)
- index on (`ingestion_run_id`)
- index on (`source_raw_record_id`)

### 11) position_lot
- `position_lot_id uuid primary key default gen_random_uuid()`
- `account_id text not null`
- `instrument_id uuid not null references instrument(instrument_id)`
- `open_event_trade_fill_id uuid not null references event_trade_fill(event_trade_fill_id)`
- `opened_at_utc timestamptz not null`
- `closed_at_utc timestamptz null`
- `open_quantity numeric(24,8) not null`
- `remaining_quantity numeric(24,8) not null`
- `open_price numeric(24,8) not null`
- `cost_basis_open numeric(24,8) not null`
- `realized_pnl_to_date numeric(24,8) not null default 0`
- `status text not null default 'open'`
- `created_at_utc timestamptz not null default now()`
- `updated_at_utc timestamptz not null default now()`

Constraints and indexes:
- check `status` in (`open`, `closed`)
- check `remaining_quantity >= 0`
- index on (`instrument_id`, `status`)
- index on (`account_id`, `instrument_id`)

### 12) pnl_snapshot_daily
- `pnl_snapshot_daily_id uuid primary key default gen_random_uuid()`
- `account_id text not null`
- `report_date_local date not null`
- `instrument_id uuid not null references instrument(instrument_id)`
- `position_qty numeric(24,8) not null`
- `cost_basis numeric(24,8) null`
- `realized_pnl numeric(24,8) not null default 0`
- `unrealized_pnl numeric(24,8) not null default 0`
- `total_pnl numeric(24,8) not null default 0`
- `fees numeric(24,8) not null default 0`
- `withholding_tax numeric(24,8) not null default 0`
- `currency text not null`
- `provisional boolean not null default false`
- `valuation_source text null`
- `fx_source text null`
- `ingestion_run_id uuid null references ingestion_run(ingestion_run_id)`
- `created_at_utc timestamptz not null default now()`

Constraints and indexes:
- `uq_pnl_snapshot_daily_account_date_instrument` unique (`account_id`, `report_date_local`, `instrument_id`)
- index on (`report_date_local`, `instrument_id`)
- index on (`provisional`, `report_date_local`)

## Required natural-key constraints from frozen spec

- `event_trade_fill`: `uq_event_trade_fill_account_exec` on (`account_id`, `ib_exec_id`)
- `event_cashflow`: `uq_event_cashflow_account_txn_action_ccy` on (`account_id`, `transaction_id`, `cash_action`, `currency`)
- `event_fx`: `uq_event_fx_account_txn_ccy_pair` on (`account_id`, `transaction_id`, `currency`, `functional_currency`)
- `event_corp_action`: `uq_event_corp_action_account_action` on (`account_id`, `action_id`), with fallback unique key for null `action_id`

## Required provenance links

- `raw_record.ingestion_run_id -> ingestion_run.ingestion_run_id`
- Canonical events include both:
  - `ingestion_run_id -> ingestion_run.ingestion_run_id`
  - `source_raw_record_id -> raw_record.raw_record_id`
- `pnl_snapshot_daily.ingestion_run_id` keeps snapshot-to-run traceability
