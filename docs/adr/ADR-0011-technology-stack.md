# ADR-0011: Technology stack

## Status
Accepted, amended by [ADR-0014](ADR-0014-sqlalchemy-and-alembic.md) (SQLAlchemy ORM replaces raw `sqlite3` — Phase 2 explicit requirement) and fulfilled by [ADR-0015](ADR-0015-htmx-alpine-frontend.md) (the optional htmx/Alpine layer below was adopted). Flask, SQLite, server-rendered Jinja, and "no client-side business logic" remain unchanged.

## Context
Constraints given: Python, Flask, SQLite, any appropriate browser tech for a rich UI, server-side app stays Flask, persistent data stays SQLite, SDD, automated tests, migrations.

## Decision
- **Language/runtime**: Python ≥ 3.12.
- **Web framework**: Flask, application-factory pattern (`create_app()`), blueprints per bounded context (`catalog`, `content`, `campaigns`, `planning`, `assets`, `api`).
- **DB access**: stdlib `sqlite3` behind a small repository layer (`app/repositories/*.py`); no full ORM. Rationale: the schema is deliberately generic/JSON-flavoured (ADR-0003, ADR-0012) where an ORM's object-mapping value is lowest, and raw SQL keeps the hand-written migrations (ADR-0007) as the single source of truth without a parallel ORM model to keep in sync. `sqlite3.Row` row factory, `PRAGMA foreign_keys=ON`, WAL journal mode for local concurrent reads.
- **Templating/UI**: Jinja2 server-rendered HTML as the baseline (works with JS disabled for core flows), progressively enhanced with vanilla JS (ES modules, no build step required) for drawers/modals, live filtering and small interactive widgets (e.g. requirement-tree expand/collapse). A small, optional bundler-free component layer (e.g. Alpine.js or htmx — decided at implementation time, recorded as an ADR update if adopted) may be introduced to avoid hand-rolled fetch/DOM glue, but the server remains the source of truth for all business logic (NFR-1).
- **CSS**: hand-written CSS with custom properties for the dark theme (NFR-9), no CSS framework dependency required; a utility layer may be added if it speeds up card/grid layouts.
- **Testing**: `pytest` + Flask test client; `pytest-cov` for coverage (NFR-3); fixtures create a temporary SQLite file per test (never the dev DB); a small fixture pack under `tests/fixtures/packs/` drives importer tests.
- **Packaging/tooling**: `pyproject.toml`, dependency pinning, `ruff` for lint/format (project has no existing convention to preserve, so this is a fresh-project choice, not forced on an existing codebase).
- **Process**: single Flask process, SQLite file under `instance/`, binds `127.0.0.1` by default (`PRODUCT_SPEC.md` §2).

## Consequences
- Minimal dependency footprint, fully offline-capable, easy to audit.
- No SPA framework/build pipeline; acceptable given desktop-first, server-rendered-first requirement and keeps the "no build step" developer experience.
- If a component layer (Alpine/htmx) is adopted during implementation, it must stay a thin progressive-enhancement layer — no business logic migrates to the client.

## Alternatives considered
- SQLAlchemy ORM: rejected for the reasons in ADR-0007; may be revisited only if the raw-SQL repository layer becomes unwieldy, as a non-breaking internal refactor (schema/migrations unaffected).
- React/Vue SPA + Flask-as-API: rejected — adds a build pipeline and a second source of UI truth; contradicts "server-side application remains Flask-based" in spirit (Flask would become a thin JSON API) and the desktop-first/server-rendered-first requirement.
