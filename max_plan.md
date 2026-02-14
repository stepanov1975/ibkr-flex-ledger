# FlexLedger — “Max Plan” Reference Architecture (All Discussed Features)

> Purpose: This document describes what FlexLedger looks like when **all** discussed features (Concept A+B+C) are implemented, so you can structure the repo and DB early without repainting yourself into a corner.

---

## 1) End-state product goals

### Core outcomes

* **Auditable ledger** of all IBKR activity (trades, cashflows, FX, fees, taxes, corporate actions), with immutable raw inputs and reproducible derived outputs.
* **Correct positions + P&L** for stocks and options (including multi-leg combos), with:

  * “Match IBKR” reconciliation mode
  * “Economic” mode (all-in costs, user-preferred basis rules)
* **Strategy intelligence**

  * Link options lifecycle events (open/close/exercise/assignment/expiration) to underlying stock legs
  * Strategy grouping (wheel, covered calls, spreads, rolls) with rollups and drill-down to atomic events
* **Label-driven portfolio analytics**

  * Labels/taxonomy, rules-based labeling, notes
  * Performance by label, strategy, underlying, time range
* **Corporate actions support**

  * Automated detection where possible
  * Manual guided resolution where ambiguous
* **FX-aware performance**

  * Track base currency (USD) + deposits in ILS + conversions
  * Attribute FX P&L separately from asset P&L
* **Self-hosted, homelab-friendly**

  * Cron ingestion
  * Reverse proxy auth
  * No paid market data required for core features (optional later)

---

## 2) Architectural principles

1. **Raw-first**: Store the Flex payload and raw rows as immutable facts. Everything else can be rebuilt.
2. **Canonical events**: Convert broker-specific rows into stable internal event types.
3. **Deterministic compute**: Positions/P&L/analytics are pure functions over events + configuration.
4. **Extensible domains**: Options/strategies/performance sit on top of the same ledger; no forked logic.
5. **Explainability**: Every number in UI can show “how we got here” with links back to raw records.
6. **Database boundary**: All SQL/ORM reads and writes live only in the database layer. Other layers consume repository/data-access interfaces and never issue direct queries.

---

## 3) System overview

### Ingestion pipeline (end-state)

1. Fetch Flex reports (scheduled; multiple queries possible)
2. Persist `ingestion_run` + raw report blob(s)
3. Parse into `raw_record` rows
4. Map into canonical events (idempotent, versioned)
5. Validate accounting invariants (balances, internal consistency)
6. Update derived state:

   * lots, positions
   * daily snapshots
   * reconciled views
   * analytics aggregates

### Major subsystems

* **Adapters**: IBKR Flex (future: CSV fallback)
* **Ledger**: canonical events + validation
* **Accounting**: lots/positions/P&L
* **Corporate Actions**: detection + adjustment workflow
* **Strategy Engine**: lifecycle linking + grouping
* **Analytics**: label/strategy/time-based stats, performance metrics
* **UI/API**: CRUD + reporting + audit trace
* **Jobs**: ingestion, reprocess, snapshots, exports

---

## 4) Repo structure (prepared for future modules)

```
flexledger/
  README.md
  pyproject.toml
  docker/
    docker-compose.yml
    Dockerfile
  scripts/
    cron/
      ingest_daily.sh
      recompute_snapshots.sh
  app/
    api/
      main.py
      deps.py
      routers/
        health.py
        auth.py                # optional (or proxy-only)
        ingestion.py
        instruments.py
        labels.py
        notes.py
        events.py              # audit views
        positions.py
        pnl.py
        strategies.py          # phase 2+
        corporate_actions.py   # phase 2+
        performance.py         # phase 3+
        exports.py
    adapters/
      ibkr_flex/
        client.py
        parser_xml.py
        parser_csv.py          # optional fallback
        mapper/
          registry.py          # schema versions
          map_trades.py
          map_cashflows.py
          map_fees.py
          map_taxes.py
          map_fx.py
          map_corp_actions.py
          map_options_lifecycle.py   # phase 2+
        fixtures/
          sample_reports/      # golden files
    domain/
      enums.py
      instruments.py
      events/
        base.py
        trade.py
        cashflow.py
        fx.py
        corporate_action.py
        options.py             # phase 2+
        lifecycle.py           # phase 2+
      strategies/
        models.py              # phase 2+
        taxonomy.py
      labels/
        models.py
      validation/
        invariants.py
        reconcile.py
    services/
      ingest_service.py
      event_service.py
      accounting/
        lot_engine.py
        position_engine.py
        pnl_engine.py
        valuation.py           # mark/MTM logic
      corporate_actions/
        detector.py
        applier.py
        ui_helpers.py
      strategies/
        linker.py
        grouper.py
        heuristics.py
      analytics/
        label_analytics.py
        instrument_analytics.py
        strategy_analytics.py
        performance_metrics.py
        fx_attribution.py
      exports/
        csv_export.py
        xlsx_export.py
    db/
      session.py
      base.py
      models/
        ingestion.py
        raw_record.py
        instrument.py
        label.py
        note.py
        event_tables.py
        derived_positions.py
        derived_snapshots.py
        strategy.py
        corp_action.py
      migrations/              # alembic
    ui/
      templates/               # Jinja2+HTMX
      static/
    cli/
      main.py                  # Typer entrypoint
      commands/
        ingest.py
        reprocess.py
        reconcile.py
        snapshot.py
        export.py
  tests/
    unit/
    integration/
    e2e/
```

**Key design choice:** keep each future domain under `services/<domain>/` and `domain/<domain>/`, so additions are “bolt-ons” not rewrites.

---

## 5) Data model (max plan)

### 5.1 Core entities

* `instrument`

  * type: STOCK, OPTION, BOND, CASH, FX_PAIR, FUND, etc.
  * relationships: underlying instrument (for options), currency
  * identifiers: symbol, conid (if available), isin/cusip (optional)
* `label`, `instrument_label`

  * support hierarchy: parent_label_id
  * optional label rules: `label_rule` (regex/symbol list/manual mapping)
* `note`

  * attach to instrument, event, strategy group, corporate action case

### 5.2 Ingestion & audit

* `ingestion_run`

  * report type, query id, checksum, timestamps, status, parser version
* `raw_report_blob`

  * compressed XML/CSV for replay
* `raw_record`

  * normalized “row” view of input
  * includes `source_row_key` to enforce idempotency

### 5.3 Canonical events (ledger)

* `event_trade_fill`

  * instrument_id, timestamp, qty, price, currency, fees, side, venue, ibkr_ref
* `event_cashflow`

  * subtype: DIVIDEND, WITHHOLDING_TAX, INTEREST, COMMISSION, DEPOSIT, WITHDRAWAL, ADJUSTMENT, etc.
* `event_fx`

  * from_ccy, to_ccy, amounts, rate, timestamp, ibkr_ref
* `event_corporate_action`

  * action_type: SPLIT, MERGER, SPINOFF, SYMBOL_CHANGE, SPECIAL_DIVIDEND, CASH_IN_LIEU, etc.
  * `requires_manual`, `case_id` link to UI workflow
* **Options lifecycle events (phase 2+)**

  * `event_option_exercise`
  * `event_option_assignment`
  * `event_option_expiration`
  * `event_option_open_close` (optional: derived from fills)
* **Strategy events (phase 2+)**

  * `event_link` table for relationships (option → stock via assignment, roll chains, etc.)

### 5.4 Derived accounting state

* `lot`

  * open lots per instrument with cost basis and metadata
  * supports FIFO/LIFO/AVG/SPECID via policy layer
* `position_snapshot_daily`

  * qty, avg_cost, mark, market_value, unrealized pnl, currency
* `pnl_snapshot_daily`

  * realized/unrealized, fees, taxes, fx attribution (optional)
* `reconciliation_snapshot`

  * compares IBKR summaries vs computed for the same date-range

### 5.5 Strategy grouping (phase 2+)

* `strategy_group`

  * name, type (wheel, spread, covered call, custom), underlying_id
* `strategy_leg`

  * links events/trades to group
* `strategy_roll_chain`

  * links groups over time (rollovers)

### 5.6 Corporate actions workflow (phase 2+)

* `corp_action_case`

  * status: OPEN/RESOLVED/IGNORED
  * proposed mapping(s), manual adjustments, audit notes

---

## 6) Accounting rules (max plan)

### 6.1 P&L modes

* **Reconciliation mode**

  * aim: match IBKR summaries
  * policy: default FIFO; optionally support tax-lot selection if you use it
* **Economic mode**

  * includes all fees and withholding tax
  * supports alternative basis policy (AVG cost, etc.)
  * includes FX attribution: separate “asset P&L” vs “FX P&L”

### 6.2 Options lifecycle correctness

* Parse OSI symbol format locally as fallback.
* Handle multi-leg combos by:

  * grouping fills into “combo trades” when IBKR identifies them
  * otherwise heuristics based on time proximity + underlying + strategy templates
* Assignment/exercise linking:

  * generate `event_link` connecting option lifecycle event to stock trade/open lot
  * allow manual overrides

### 6.3 Corporate actions handling

* Baseline:

  * detect and flag
  * stop automated compute if action impacts cost basis/qty unless resolved
* Automated where possible:

  * simple splits, symbol changes if IBKR provides enough metadata
* Manual workflow for complex:

  * merger consideration, spinoffs, adjusted option deliverables

---

## 7) Analytics (max plan)

### 7.1 Label-driven reporting

* P&L by label (total, realized, unrealized, fees, taxes, FX attribution)
* Heatmaps/trends by month
* Concentration by label and by underlying
* “Top contributors / detractors” by label

### 7.2 Strategy analytics (phase 2+)

* Performance by strategy type
* Premium earned vs assignment loss/gain
* Win rate / expectancy / average days in trade
* Roll frequency and roll outcomes

### 7.3 Performance metrics (phase 3+)

* Money-weighted return (IRR) per label/strategy
* Time-weighted return (TWR) for overall portfolio and subsets
* Drawdown, volatility (optional)
* Cashflow-aware benchmarking (optional; requires external index data if desired)

### 7.4 FX analytics

* Realized FX P&L on conversions and settlements
* Exposure by currency
* Attribution: what part of returns came from FX vs asset movement

---

## 8) UI (end-state navigation)

### Core

* Dashboard: totals + reconciliation status + ingestion status
* Instruments:

  * list, detail
  * labels, notes, history, P&L
* Labels:

  * taxonomy tree, assignment, rules
  * label performance pages
* Events:

  * “ledger view” with filters and raw trace links
* Positions & P&L:

  * current positions, snapshots, realized/unrealized breakdowns

### Options & strategies (phase 2+)

* Strategy groups list + builder UI
* Strategy detail:

  * timeline of events
  * roll chain view
  * premium earned / assignment links

### Corporate actions (phase 2+)

* “Cases” queue
* Resolver UI: propose mapping, confirm adjustments, regenerate derived state

### Exports

* CSV/XLSX reports by date range, label, instrument, strategy

---

## 9) API design (end-state pattern)

* `/ingestion/runs` (list/status/logs)
* `/instruments`, `/instruments/{id}`
* `/labels`, `/labels/{id}`, `/labels/{id}/rules`
* `/notes`
* `/events` (filterable audit endpoint)
* `/positions` (current) + `/snapshots` (historical)
* `/pnl` + `/pnl/labels` + `/pnl/strategies`
* `/strategies/*` (phase 2+)
* `/corporate-actions/*` (phase 2+)
* `/exports/*`

**Design preference:** the UI uses these same endpoints; HTMX can keep it simple without SPA complexity.

---

## 10) Jobs & operations (end-state)

### Scheduled jobs

* Daily ingestion (cron)
* Daily snapshots recompute (after ingestion)
* Periodic reconciliation checks
* Optional: reprocess after mapper changes

### Observability

* structured logs (json)
* ingestion run log and diff summary
* “data quality” alerts:

  * missing sections
  * unmatched cashflows
  * unresolved corp action cases

---

## 11) Implementation roadmap (max plan)

### Phase 1 (MVP+): Stocks + labels + audit

* Flex ingestion + raw storage
* canonical events: stock fills, fees, dividends, withholding, FX
* positions + realized/unrealized P&L
* labels + notes + label performance reports
* reconciliation scaffolding (even if partial)

### Phase 2: Options lifecycle + strategy rollups

* OSI parsing
* assignment/exercise/expiration events
* event linking + manual override UI
* strategy groups + roll chains + strategy analytics v1

### Phase 3: Performance analytics

* TWR/MWR, cashflow-aware attribution
* FX P&L attribution
* richer label/strategy performance dashboards

### Phase 4: Corporate actions workflow hardening

* auto-detection improvements
* guided resolution + replay compute
* adjusted options deliverable support

### Phase 5: Nice-to-haves

* CSV import fallback
* exports, scheduled email reports
* multi-account support (still single-user)

---

## 12) Preparation checklist (so structure survives future scope)

* ✅ Postgres from day 1
* ✅ raw report blobs + raw records stored immutably
* ✅ canonical event tables separate from derived tables
* ✅ policy layer for cost basis (FIFO/AVG/etc.)
* ✅ event linking mechanism designed early (`event_link`)
* ✅ corporate action “case” concept exists early (even if unused in v1)
* ✅ strategy module placeholder exists (even if empty)

---

If you want, I can also generate a **starter repo scaffold** (folders + empty modules + Alembic baseline + Docker Compose + Typer CLI skeleton) so you can `git init` and start implementing immediately with the structure already aligned to this max plan.
