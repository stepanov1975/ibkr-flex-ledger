# Architecture Conventions

Date: 2026-02-14
Scope: Runtime foundation conventions for Task 1

## Purpose

This document defines mandatory architecture boundaries for project-native runtime code.
It applies to all modules outside `references/`.

## Layer Boundaries

- `app/adapters`: external system integration boundaries.
- `app/mapping`: raw-to-canonical transformation boundaries.
- `app/ledger`: position and PnL computation boundaries.
- `app/analytics`: reporting aggregation boundaries.
- `app/api`: FastAPI application and route composition boundaries.
- `app/jobs`: workflow orchestration boundaries.
- `app/db`: database access boundaries.

## Database Access Rule (Mandatory)

- All SQL and ORM operations must be implemented only in `app/db` modules.
- Modules in `app/adapters`, `app/mapping`, `app/ledger`, `app/analytics`, `app/api`, and `app/jobs` must use db-layer interfaces.
- Direct SQL or ORM usage outside `app/db` is prohibited.

## Reference Boundary Rule (Mandatory)

- Code in `references/` is read-only reference material.
- Project runtime code must not import modules from `references/`.
- Project runtime code must not invoke `references/` CLI entry points.
- Reference repositories may inform design, naming, and validation patterns only.

## Foundation Framework Choice

- FastAPI is the API/UI framework for MVP foundation implementation.
- Additional framework choices must not violate the layer boundaries in this document.
