# Database Migrations

Date: 2026-02-14
Scope: Task 2 migration workflow

## Prerequisites

- PostgreSQL reachable via `DATABASE_URL`.
- Python environment with dependencies from `requirements.txt` installed.

## Docker-only database guidance

This project standardizes on Docker PostgreSQL for local runtime and migration workflows.

Set `DATABASE_URL` according to where the migration command runs:

- Command executed in host shell:

```bash
export DATABASE_URL=postgresql+psycopg://stock_user:stock_password@127.0.0.1:5433/stock_app
```

- Command executed inside Docker network:

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
