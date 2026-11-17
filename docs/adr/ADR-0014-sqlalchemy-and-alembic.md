# ADR-0014: SQLAlchemy ORM + Alembic-managed migrations (supersedes ADR-0007, amends ADR-0011)

## Status
Accepted (Phase 2 implementation)

## Context
`ADR-0007` chose hand-written, forward-only SQL migration files plus a bespoke `schema_migrations` tracker, and `ADR-0011` chose raw stdlib `sqlite3` behind a repository layer, explicitly rejecting SQLAlchemy to avoid a parallel source of truth alongside hand-written DDL.

The Phase 2 implementation brief explicitly requires **SQLAlchemy** and **proper database migrations** as part of the mandated backend stack, alongside Flask's application factory pattern. This is a direct, explicit instruction for this phase and overrides the earlier preference recorded in ADR-0007/ADR-0011. Re-litigating raw-SQL-vs-ORM in-session would stall implementation; the pragmatic and correct move is to accept the new instruction as the current decision and update the record rather than silently diverge from either the brief or the docs.

## Decision
1. **SQLAlchemy** (2.x, via `Flask-SQLAlchemy`) is the ORM. Every table in `DATABASE_SCHEMA.md` is declared as a SQLAlchemy model under `app/models/`, grouped exactly as the schema doc groups them (`catalog.py`, `assets.py`, `campaign.py`, `strategy.py`). Column types, nullability, `CHECK` constraints (via SQLAlchemy `CheckConstraint`), uniqueness and FK `ondelete` behaviour are declared in the model to match `DATABASE_SCHEMA.md` **exactly** — the schema doc remains the authoritative description of the *intended* structure; the models are its code expression, not a second, divergent source of truth.
2. **Alembic** (via `Flask-Migrate`) manages migrations under `migrations/` (Alembic's own directory layout: `migrations/env.py`, `migrations/versions/*.py`). This still satisfies `ADR-0007`'s underlying intent — forward-only, numbered (Alembic's revision chain is a strict linear/branching history with explicit `down_revision` pointers), applied via a single command (`flask db upgrade` replaces the bespoke `flask db migrate` custom runner) — so the *principle* of ADR-0007 (schema changes only via tracked, ordered migration files, never ad hoc `ALTER`) is retained; only the tooling changes.
3. Repository modules (`app/repositories/*.py`) now wrap SQLAlchemy `Session` queries instead of raw `sqlite3`/parameterized SQL strings. The campaign-isolation convention from `ADR-0010` (every campaign-scoped repository function takes `campaign_id` as a mandatory first argument and filters by it) is unchanged and enforced the same way, just expressed as `.filter_by(campaign_id=campaign_id)` instead of a `WHERE` clause.
4. `PRAGMA foreign_keys=ON` is enabled via a SQLAlchemy engine `connect` event listener (since SQLite does not enable it by default even under SQLAlchemy). WAL mode is enabled the same way.

## Consequences
- `ADR-0007` is **superseded**: no more hand-written `migrations/000N_*.sql` files or bespoke `schema_migrations` table; Alembic owns its own `alembic_version` bookkeeping table instead (functionally the same purpose as the `schema_migrations` table originally specified in `DATABASE_SCHEMA.md` §5.1 — that table is dropped from the schema doc in favour of Alembic's own).
- `ADR-0011` is **amended**, not reversed in full: Flask, SQLite, server-rendered Jinja and the no-ORM-for-domain-logic principle (`domain/*` stays pure, framework/ORM-free, per `ARCHITECTURE.md` §2) all still hold; only the "raw `sqlite3`, no ORM" clause is replaced by "SQLAlchemy ORM, repositories wrap `Session`".
- `tests/test_migrations.py` (per `ARCHITECTURE.md` §10) now asserts that `alembic upgrade head` on a fresh temp DB produces a schema matching the SQLAlchemy metadata (`Base.metadata`) and spot-checks against `DATABASE_SCHEMA.md`'s documented tables/columns, rather than diffing hand-written SQL.
- Future schema changes are authored as `flask db migrate -m "..."` (autogenerate a draft from model changes) followed by manual review/edit of the generated revision before commit — autogeneration is a drafting aid, not a substitute for reviewing the diff against `DATABASE_SCHEMA.md`, which must still be updated in the same change.

## Alternatives considered
- Keep ADR-0007/ADR-0011 as written and push back on the brief: rejected — the brief is an explicit, direct instruction for this implementation phase from the project owner, not an ambiguous requirement to interpret; SDD process (`ADR-0001`) calls for updating the docs when such a decision is made, not for blocking on a stale prior decision.
- SQLAlchemy models with Alembic autogenerate as the *only* source of truth (drop `DATABASE_SCHEMA.md`'s hand-maintained tables): rejected — the schema doc is still the cross-referenced, human-readable contract used by `DOMAIN_MODEL.md`, `ARCHITECTURE.md` and the ADRs; keeping both in sync (model ⇔ doc) is more auditable than deleting the doc.
