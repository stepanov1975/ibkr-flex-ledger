# IBKR Flex Ledger

Self-hosted portfolio accounting and analytics for Interactive Brokers (IBKR) Flex reports. Track holdings, profit and loss, cash movements, and costs, with an audit trail back to your broker data.

## Features

- **Portfolio overview:** View positions, realized and unrealized profit and loss, cash balances by currency, net transfers, and estimated portfolio value and profit when the required data is available.
- **Stock history:** Open a symbol to explore its trades, related options, cashflows, corporate actions, and open or closed FIFO lots, including partial closes.
- **Costs and dividends:** Review commissions, interest, taxes, other fees, and dividend payments. Break securities commissions down by instrument type and buy/sell side.
- **Transfer history:** Browse deposits and withdrawals in their original currencies.
- **Labels and notes:** Organize instruments for grouped analysis and add context to your records.
- **Reports and reconciliation:** Export profit-and-loss and reconciliation reports as CSV, compare broker and calculated values, and trace results to the original report rows.
- **Corporate-action review:** Preview and apply supported split corrections, security transfers, and distribution treatments. Actions requiring unsupported accounting remain flagged for review.
- **Import monitoring:** Inspect import history and diagnostics, replay stored reports, and monitor scheduled-import reliability. Optional webhook and email notifications report alert and recovery transitions.

The app focuses on stocks, with supported option trades and broker position valuation. It does not provide a full options lifecycle, real-time market data, risk dashboards, or trade execution. Reports with missing inputs, unresolved accounting, or outdated calculations are marked provisional; unavailable values display as `N/A`.

Dates display as `dd/mm/yy`; timestamps use 24-hour time in `Asia/Jerusalem`. Business dates retain the date reported by IBKR.

## Deployment

### Requirements

- A host with Docker Engine and Docker Compose, and a checkout of this repository.
- An IBKR Flex Web Service token and an XML Flex query for a single account with USD base currency.
- A trusted network for access. The app has no built-in authentication, and Compose publishes its application and database ports on the host. Restrict access to your trusted LAN.

Use the PostgreSQL service included in Compose. A separate host PostgreSQL installation is unnecessary.

### 1. Configure your Flex query

Include these sections: `Trades`, `OpenPositions`, `CashTransactions`, `CorporateActions`, `ConversionRates`, `SecuritiesInfo`, and `AccountInformation`. Include `CashReport` for cash balances and portfolio totals, and `MTMPerformanceSummaryInBase` and `FIFOPerformanceSummaryInBase` for reconciliation.

The report must contain one statement for one account, with its account ID in the statement header or account information. See the [Flex query field catalog](docs/flex_query_fields.md) for field details.

### 2. Configure the application

Run all commands from the repository root. Create your local configuration:

```bash
cp .env.example .env
```

Edit `.env` before starting:

| Setting | What to enter |
| --- | --- |
| `IBKR_FLEX_TOKEN` | Your Flex Web Service token; replace the placeholder. |
| `IBKR_FLEX_QUERY_ID` | Your Flex query ID; replace the placeholder. |
| `ACCOUNT_ID` | A stable identifier for the account, normally your IBKR account ID. |
| `POSTGRES_PASSWORD` | Replace the example database password. |
| `APPLICATION_PORT` | Browser access port; defaults to `8000`. |
| `POSTGRES_PORT` | Host database port; defaults to `5433`. |

Keep the other template settings unless your deployment needs different values. If you change database credentials, update the template's `DATABASE_URL` for host-shell commands too. Compose configures the app container's database connection from the `POSTGRES_*` settings.

Keep credentials in the local, gitignored `.env` file.

### 3. Start the application

Build and start the app and database:

```bash
docker compose up -d --build
docker compose ps
```

Startup applies database migrations automatically. Check [application health](http://127.0.0.1:8000/health), then open the [portfolio dashboard](http://127.0.0.1:8000/ui). From another machine, replace `127.0.0.1` with the server's LAN IP; use your configured port if it differs from `8000`.

If startup fails, inspect the logs:

```bash
docker compose logs --tail=100 app postgres
```

### Alternative: use a published image

Release images are published to `ghcr.io/stepanov1975/ibkr-flex-ledger` for `linux/amd64`. To use a specific release instead of building locally, save this override as `compose.release.yml`, replacing the example tag with your chosen published release:

```yaml
services:
  app:
    image: ghcr.io/stepanov1975/ibkr-flex-ledger:v1.1.0
```

Start it alongside the Compose database and your `.env` configuration:

```bash
docker compose -f docker-compose.yml -f compose.release.yml up -d --no-build --pull always
```

`latest` follows the latest stable GitHub release; use a specific release tag for a repeatable deployment. Private packages require `docker login ghcr.io` with a token that has `read:packages` permission. Use both Compose files for subsequent commands when deploying this way.

## Import and explore your data

Trigger your first import:

```bash
docker compose exec -T app python -m app.main ingestion-run
```

Review the result in [ingestion run history](http://127.0.0.1:8000/ui/ingestion-runs). If a report fails with `MISSING_REQUIRED_SECTION`, add the listed sections to your IBKR query and retry. Failed imports preserve the previous ledger; original downloaded reports are retained for audit and troubleshooting.

| Page | What you can do |
| --- | --- |
| [Portfolio](http://127.0.0.1:8000/ui) | Review holdings and totals; select a symbol for its history. Zero-position instruments are hidden by default and can be revealed. |
| [Costs](http://127.0.0.1:8000/ui/costs) | Explore fees, taxes, interest, and commission breakdowns. |
| [Transfers](http://127.0.0.1:8000/ui/transfers) | Browse deposits and withdrawals. |
| [Operations](http://127.0.0.1:8000/ui/operations) | Monitor import reliability and review corporate actions. |
| [Import history](http://127.0.0.1:8000/ui/ingestion-runs) | Inspect run status and diagnostics. |

## Scheduling, backups, and upgrades

Starting Compose does not install scheduled jobs. On a Linux host with systemd, follow the [scheduler setup](deploy/systemd/README.md) to enable daily imports, daily backups and diagnostics retention, and weekly restore drills. The supplied units assume the repository is at `/stock_app`; adjust their paths if yours differs. Enable the optional alert timer after configuring webhook or email delivery.

PostgreSQL data lives in the persistent `postgres_data` volume. Additional volumes retain archived database logs, backups, and diagnostics. `docker compose down` preserves volumes; adding `--volumes` deletes them.

Follow the [operations guide](docs/operations.md) for verified backups, restore procedures, alerts, and recovery. Back up before upgrading. Update your checkout to the desired release and rerun `docker compose up -d --build`, or update the image tag and rerun the published-image command. Startup applies migrations; restart any separately running import or replay workers with the same version. See [migration guidance](docs/migrations.md) for maintenance-window considerations.
