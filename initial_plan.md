# IBKR Trade Ledger & Portfolio Insights (Homelab) — MVP Spec

## 1) Project purpose

Build a self-hosted web app that imports **Interactive Brokers (IBKR)** activity data via **Flex Web Service / Flex Queries** and computes **auditable, reconciliable portfolio metrics** (starting with stocks), with **labels + notes** for grouping and analysis.

Flex Web Service is designed specifically for programmatic retrieval of preconfigured Flex Queries (templates you define in IBKR first). ([Interactive Brokers][1])
(And yes: many people use Flex because broker APIs are poor at historical trade history; Flex is the practical route.) ([GitHub][2])

---

## 2) MVP goals (Phase 1)

### 2.1 Data ingestion (must have)

* Fetch Flex report daily (cron) and store:

  * **raw report payload** (for replay/debug)
  * **normalized events** (trades, cashflows, FX, fees, withholding tax, positions snapshots if available)
* Detect schema drift (missing/renamed fields) and fail loudly with good diagnostics.

### 2.2 Core portfolio functionality (stocks-first)

* Positions:

  * current quantity, average cost, cost basis (base currency)
* P&L:

  * realized P&L per instrument
  * unrealized P&L per instrument (using IBKR-provided marks/snapshots in Flex when available)
* Costs:

  * commissions/fees included in “economic P&L”
  * withholding tax tracked explicitly

### 2.3 Labels + notes (your primary v1 UX)

* Create labels (e.g., `Software`, `Semiconductors`, `Dividend`, `High Volatility`)
* Assign multiple labels per stock
* Add notes to:

  * instrument (stock)
  * optionally, a specific event/trade (nice-to-have in MVP)
* Reporting:

  * P&L by label (sum across member instruments)
  * drilldown: label → instruments → events

### 2.4 Reconciliation (debug/verification mode)

* Two views of the same results:

  1. **Reconciliation mode**: “match IBKR as close as practical” (initially FIFO or broker-like lot method)
  2. **Economic mode**: includes all fees/withholding and can support alternative cost basis methods later
* Show diffs by day and by instrument and link back to underlying raw Flex rows.

---

## 3) Future goals (Phase 2+)

### Phase 2 — Options lifecycle & strategy grouping

* Parse OCC/OSI option symbols locally (no paid API required):

  * root padded to 6 chars, expiry `YYMMDD`, right `C/P`, strike = last 8 digits / 1000 ([Wikipedia][3])
* Support:

  * multi-leg combos
  * exercises/assignments/expirations
  * “strategy groups” (rolls / wheels / spreads) with ability to roll up P&L while keeping atomic events visible
* Corporate actions impact on options:

  * adjusted/non-standard contracts (e.g., symbol suffixes after splits) and deliverable changes ([infomemo.theocc.com][4])

### Phase 3 — Performance analytics (cashflow & FX aware)

* Money-weighted / time-weighted returns
* Performance by:

  * label
  * strategy group
  * time period
* FX:

  * track realized/unrealized FX impact across deposits, conversions, and holdings

### Phase 4 — Quality-of-life and scaling

* CSV upload fallback (manual import)
* Export reports (CSV/XLSX)
* Automated label rules (symbol lists, regex, manual mapping tables)
* Multi-account support (if ever needed)

---

## 4) Non-goals (explicitly out of scope for MVP)

* Real-time market data, Greeks, live risk dashboards (requires market data subscriptions or paid APIs)
* Trade execution / broker automation
* Fully automatic handling of every corporate action edge case (MVP will **flag** and allow **manual intervention**)

---

## 5) Target environment & deployment

* Runtime: Ubuntu LXC container, homelab
* Access: behind reverse proxy with auth
* Deployment: Docker Compose recommended:

  * `app` (FastAPI)
  * `db` (PostgreSQL)
  * optional: `adminer` / `pgadmin` for debugging
* Scheduling: cron invoking an ingestion CLI (simplest, reliable)

---

## 6) Technical architecture (modular by design)

### 6.1 Recommended stack

* API/backend: FastAPI + Pydantic + SQLAlchemy + Alembic
* DB: PostgreSQL
* Flex XML parsing: `lxml` (and/or reuse a proven parser library)
* Testing: pytest + golden fixtures

### 6.2 Boundary rules (to keep Phase 2/3 easy)

* **Adapters**: “IBKR Flex → raw records”
* **Mappers**: “raw records → canonical events”
* **Ledger/Accounting**: “events → positions, P&L”
* **Analytics**: “positions/P&L → group views (labels/strategies)”
* **UI/API**: CRUD + reporting endpoints

Key discipline: **raw payload is immutable**; derived tables can be regenerated.

---

## 7) Data model (MVP-level)

### Core entities

* `instrument`

  * symbol, name, type=STOCK, currency
* `label`
* `instrument_label` (many-to-many)
* `note` (entity_type + entity_id + text + timestamps)

### Ingestion traceability

* `ingestion_run` (when, which Flex query, status)
* `raw_record` (ingestion_run_id, record_type, payload_json, source identifiers)

### Canonical events (stocks-first)

* `event_trade_fill` (timestamp, instrument, qty, price, fees, currency, ibkr_ref)
* `event_cashflow` (timestamp, instrument nullable, amount, subtype: dividend/withholding/fee/etc)
* `event_fx` (timestamp, from_ccy, to_ccy, from_amt, to_amt, rate)
* `event_corp_action` (timestamp, instrument, action type, payload, requires_manual)

### Derived outputs

* `position_lot` (to support FIFO now; enables AVG later without rewriting)
* `pnl_snapshot_daily` (optional but very useful for label reports and reconciliation)

---

## 8) Flex Query configuration (inputs you should enable)

Create Flex Queries in IBKR first, then retrieve via Flex Web Service. ([Interactive Brokers][1])

A practical “include these sections” checklist exists in the wild (useful as a starting point), and it explicitly includes options lifecycle sections you’ll want later. ([GitHub][5])

**MVP must-have sections (typical):**

* Trades / fills (stock)
* Open positions / positions
* Dividends
* Withholding tax
* Fees/commissions
* FX conversions / exchange rates
* Corporate actions (at minimum: events that IBKR exposes)
* (Phase 2) Option Exercises / Assignments / Expirations ([GitHub][5])

---

## 9) Implementation plan

### Milestone 0 — Project skeleton (1–2 days)

* Repo structure
* Docker Compose: app + Postgres
* Alembic migrations
* Basic auth via reverse proxy (app assumes trusted perimeter)

### Milestone 1 — Flex ingestion + raw storage

* Configure Flex Web Service credentials and query ID
* Implement:

  * download Flex report
  * store raw payload + metadata
  * parse + persist raw rows as `raw_record`

### Milestone 2 — Canonical events (stocks + cashflows + FX)

* Map raw records → events:

  * stock fills
  * fees/commissions
  * dividends + withholding
  * FX conversions
* Add “reprocess” command: regenerate events from raw inputs

### Milestone 3 — Positions & P&L engine (stocks)

* Lot engine (FIFO first; keep it pluggable)
* Realized/unrealized P&L per instrument and totals
* Base currency handling (USD) + FX events tracked

### Milestone 4 — Labels + notes + reporting

* CRUD for instruments, labels, assignments, notes
* Report: P&L by label + drilldown views
* Basic filters: date range, label, instrument

### Milestone 5 — Reconciliation UI

* Compare computed totals vs IBKR-reported summaries (where present)
* Diff tooling + link-back to raw records for audit

**Definition of done for MVP**

* Daily cron ingestion works end-to-end
* You can label stocks and see label-group performance
* You can explain any number by tracing it to raw Flex data
* Known edge cases are flagged clearly (corp actions, missing sections)

---

## 10) Risks & mitigations (pre-implementation checklist)

1. **Flex schema drift**

* Mitigation: raw storage + versioned mappers + test fixtures + strict validation

2. **Reconciliation mismatches**

* Mitigation: explicit “IBKR match mode” and show diffs; do not hide them

3. **Corporate actions complexity**

* Mitigation: “flag + manual apply” workflow for rare complicated cases (MVP)

4. **Options lifecycle ambiguity (Phase 2)**

* Mitigation: auto-link heuristics + manual override UI

---

## 11) Reference projects (examples to learn from)

### Flex parsing / downloading

* `csingley/ibflex` — Python Flex XML parser and client patterns ([GitHub][6])
* `cubinet-code/flexquery` — small Python downloader using official Flex Web Service API v3, with plugin-ish transforms ([GitHub][7])
* `agusalex/IBFlexQueryAPIProxy` — minimal API wrapper around Flex downloading using `ibflex` ([GitHub][8])
* Official IBKR Flex Web Service docs (how it’s intended to work) ([Interactive Brokers][1])

### End-to-end “ingest → store → analyze”

* `alexpung/IB_Flex` — Flex download + SQLite + analysis + web UI concepts ([GitHub][9])
* `bankroll-py/bankroll` — uses Flex Web Service for trade history; good operational notes ([GitHub][2])
* `westonplatter/finx-reports-ib` — practical checklist of Flex sections to include (very relevant for correctness) ([GitHub][5])

### UX/analytics inspiration (esp. options later)

* `Marfusios/premium-tracker` — “IBKR Portfolio Analyzer” focusing on statement → actionable analytics UX ([GitHub][10])

### Accounting / reconciliation mindset (read for ideas; mind licenses/languages)

* `alensiljak/interactive-brokers-flex-rs` — compares Flex transactions vs Ledger; shows mapping discipline ([GitHub][11])

---

## 12) Open questions to settle before coding (quick decisions)

* Realized P&L method for “IBKR match mode” (FIFO is typically the safest default)
* Which Flex report sections you will *guarantee* to include (lock this early)
* Base currency rules for FX conversions and deposits/withdrawals
* Whether “notes” must support attachments (likely no for MVP)

---

## Appendix: raw links for references

```text
https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/
https://www.interactivebrokers.com/campus/glossary-terms/flex-web-service/

https://github.com/csingley/ibflex
https://github.com/cubinet-code/flexquery
https://github.com/agusalex/IBFlexQueryAPIProxy
https://github.com/alexpung/IB_Flex
https://github.com/bankroll-py/bankroll
https://github.com/westonplatter/finx-reports-ib
https://github.com/Marfusios/premium-tracker
https://github.com/alensiljak/interactive-brokers-flex-rs

https://en.wikipedia.org/wiki/Option_symbol
https://infomemo.theocc.com/infomemos?number=26853
```

If you want, I can convert this into a `README.md` + `docs/` layout (MVP spec, Flex configuration guide, DB schema notes, and an ingestion troubleshooting playbook) so it’s ready to drop into a new repo.

[1]: https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/?utm_source=chatgpt.com "Flex Web Service - IBKR Campus"
[2]: https://github.com/bankroll-py/bankroll?utm_source=chatgpt.com "bankroll-py/bankroll: Ingest portfolio and other data from ..."
[3]: https://en.wikipedia.org/wiki/Option_symbol?utm_source=chatgpt.com "Option symbol"
[4]: https://infomemo.theocc.com/infomemos?number=26853&utm_source=chatgpt.com "OCC Infomemo #26853 - Contract Adjustments And The ..."
[5]: https://github.com/westonplatter/finx-reports-ib?utm_source=chatgpt.com "Compose custom reports from IBKR Flex Statements."
[6]: https://github.com/csingley/ibflex?utm_source=chatgpt.com "Python parser for Interactive Brokers Flex XML statements"
[7]: https://github.com/cubinet-code/flexquery?utm_source=chatgpt.com "cubinet-code/flexquery"
[8]: https://github.com/agusalex/IBFlexQueryAPIProxy?utm_source=chatgpt.com "agusalex/IBFlexQueryAPIProxy: An API to allow ..."
[9]: https://github.com/alexpung/IB_Flex?utm_source=chatgpt.com "alexpung/IB_Flex: Assorted python scripts for downloading ..."
[10]: https://github.com/Marfusios/premium-tracker?utm_source=chatgpt.com "Marfusios/premium-tracker - GitHub"
[11]: https://github.com/alensiljak/interactive-brokers-flex-rs?utm_source=chatgpt.com "alensiljak/interactive-brokers-flex-rs: Tools to assist with IB ..."
