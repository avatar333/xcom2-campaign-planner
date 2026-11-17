# ADR-0007: Forward-only, numbered, hand-written SQL migrations

## Status
Superseded by [ADR-0014](ADR-0014-sqlalchemy-and-alembic.md) — the Phase 2 implementation brief mandated SQLAlchemy + proper (Alembic) migrations. The *principle* below (ordered, forward-only, tracked migrations; no ad hoc `ALTER`) is retained under Alembic; only the hand-written-SQL tooling choice is replaced. Kept for historical record.

## Context
Schema changes must use migrations (constraint). The app is SQLite-based, single-user, local-first; introducing a heavy ORM-migration framework (Alembic) is viable but adds a dependency and abstraction layer that can obscure exactly what SQL runs against SQLite's particular ALTER TABLE limitations (e.g. limited `ALTER TABLE ... DROP COLUMN`/constraint changes pre-3.35, no `ALTER COLUMN`).

## Decision
Migrations are plain numbered SQL files: `migrations/0001_initial.sql`, `migrations/0002_add_campaign_plans.sql`, etc., each wrapped in a single transaction, forward-only (no generated "down" migrations — rollback is "restore from backup" per NFR/A-level single-user tool). A `schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)` table tracks applied versions. `flask db migrate` applies all pending files in order inside `sqlite3` with foreign_keys=ON. Each migration file is paired with an entry in `DATABASE_SCHEMA.md`'s changelog so the documented schema and the migration history never diverge. A test (`tests/test_migrations.py`) applies all migrations to a fresh temp DB and asserts the resulting `sqlite_master` matches the documented table/column/index list.

Use Alembic is explicitly deferred: if the project later needs multi-branch/down-migrations, a thin Alembic wrapper around the same SQL-file convention can be introduced without changing the DB.

## Consequences
- Full transparency of DDL (important when teaching another session/developer how precedence, JSON columns and generated columns are declared).
- No ORM is required for schema evolution; the app may still use a lightweight data-mapper or raw `sqlite3`/`sqlite-utils` for queries (see ADR-0011).
- Discipline required: every PR touching the schema must add both a migration file and the corresponding `DATABASE_SCHEMA.md` edit; CI/test enforces drift detection.

## Alternatives considered
- Alembic/SQLAlchemy migrations: rejected for v1 — extra dependency and abstraction over a database (SQLite) where several Alembic SQLite recipes (batch mode) exist specifically to work around limitations the project would rather make explicit in hand-written SQL.
- Code-generated schema from Python models (e.g. Django-style): rejected — the brief explicitly keeps Python free of a competing source of truth; SQL migrations + `DATABASE_SCHEMA.md` are the single source of truth for structure.
