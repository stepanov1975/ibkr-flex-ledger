# Database Migrations

Scope: The full Alembic migration chain, from the initial schema through the current head.

The [original schema contract](archive/task2_schema_contract.md) describes revision
`20260214_01` only. Use [migration files](../alembic/versions) and
[database metadata](../app/db) for subsequent schema changes.

## Prerequisites

- PostgreSQL reachable via `DATABASE_URL`.
- Python environment with dependencies from `requirements.txt` installed.

## Docker-only database guidance

This project standardizes on Docker PostgreSQL for local runtime and migration workflows.

Set `DATABASE_URL` according to where the migration command runs:

- Command executed in host shell:

Recommended default in local `.env` for host-shell commands (`alembic`, `pytest`, `python -m app.main`):

```bash
export DATABASE_URL=postgresql+psycopg://stock_user:stock_password@127.0.0.1:5433/stock_app
```

- Command executed inside Docker network:

Use this only when the migration command itself runs inside Docker network context:

```bash
export DATABASE_URL=postgresql+psycopg://stock_user:stock_password@postgres:5432/stock_app
```

## Commands

Run migrations to head:

```bash
alembic upgrade head
```

Create a new migration revision:

```bash
alembic revision -m "describe change"
```

Downgrade one revision:

```bash
alembic downgrade -1
```

## Configuration behavior

- Alembic is configured in `alembic.ini` with scripts in `alembic/`.
- Database URL is loaded from project settings contract through `app.config.config_load_database_url()`.
- `.env` values are supported via the shared settings model.

## Deployment considerations

Container startup runs `alembic upgrade head`. Back up before upgrading and deploy
application code and database changes together; restart separate ingestion/replay workers
with the same version. See the [operations guide](operations.md) for backup and recovery.

The incremental-ingestion indexes use transactional `CREATE INDEX`; use a maintenance
window for large tables because index creation can block writes. Revision `20260911_17`
requires an online migration to backfill broker identity from successful artifacts and
stops on unreadable or ambiguous account evidence.

Inspect the revision chain and applied state:

```bash
alembic history
alembic current
alembic heads
```

A downgrade can remove data and is not a substitute for the verified restore procedure.
