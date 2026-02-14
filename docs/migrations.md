# Database Migrations

Date: 2026-02-14
Scope: Task 2 migration workflow

## Prerequisites

- PostgreSQL reachable via `DATABASE_URL`.
- Python environment with dependencies from `requirements.txt` installed.

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
