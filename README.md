# XCOM 2 Campaign Planner

A locally-run, visually rich web app for planning and tracking one or more **XCOM 2** / **War of the Chosen** / **Long War of the Chosen** / modded campaigns. It is a planner and tracker — not a save-game editor, not a tactical simulator.

## What it does

- **Multiple independent campaigns** — each playthrough (e.g. "Commander Vanilla", "LWOTC 2026") has its own name, difficulty, notes and state. Deleting or editing one never touches another.
- **Content sources** — the base game, DLC, LWotC, individual Steam Workshop mods and your own custom content are all first-class, independently toggleable sources with provenance (kind, version, Workshop ID, license/attribution).
- **Per-campaign source selection** — a campaign enables an ordered set of content sources (precedence = which source "wins" if two define the same thing). Switching campaigns changes what the catalog shows, without ever duplicating data.
- **Data-driven catalog** — weapons, armour, research, facilities, abilities, enemies and any other object type are rows, not Python code. New object types, attributes and relationship kinds can be added without a schema migration.
- **Catalog objects** — browse/search/filter by type, source and tag; create custom/mod objects by hand; upload images (validated, re-encoded, content-addressed storage); built-in objects are read-only and are customized via non-destructive **overlays**.
- **Relationships** — generic typed links between any two objects (prerequisite, unlocks, requires-resource, compatible-with, etc.), with validation against self-links, duplicates and direct cycles.
- **Import/export** — catalog data can be exported to JSON and re-imported elsewhere, with dry-run validation and conflict detection (existing records are never silently overwritten).
- **Dark, XCOM-inspired UI** — persistent left navigation, image-forward catalog cards, reusable components (badges, modals, empty states), htmx/Alpine for snappy interactions without a JS build step.

Not yet implemented: campaign progression/state tracking, the planning engine ("what should I research next"), soldiers/loadouts, and the curated base/WotC/LWotC data importer. See `docs/IMPLEMENTATION_PLAN.md` for what's next.

## Documentation

This project follows Spec-Driven Development. The specification is the source of truth, under [`docs/`](docs/):

| Doc | Covers |
|---|---|
| `docs/PRODUCT_SPEC.md` | Requirements, functional/non-functional, success criteria |
| `docs/DOMAIN_MODEL.md` | Core concepts and glossary |
| `docs/ARCHITECTURE.md` | Module layout, request flow, data formats |
| `docs/DATABASE_SCHEMA.md` | Authoritative table/column/index reference |
| `docs/UI_UX_SPEC.md` | Screens, components, visual language |
| `docs/IMPLEMENTATION_PLAN.md` | Phased delivery plan and current status |
| `docs/adr/` | Architecture decision records |

## Tech stack

Python 3.11+, Flask (application factory), SQLAlchemy + Alembic, SQLite, Jinja2 + htmx + Alpine.js, Pillow for images. No external services; works fully offline.

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
export FLASK_APP=wsgi.py && export $(grep -v '^#' .env | xargs)

flask db upgrade      # create the schema
flask seed-dev        # seed content sources + builtin object/relationship types (no game data)
flask run --port 5050 # http://127.0.0.1:5050
```

## Tests

```bash
pytest                                      # full suite
pytest --cov=app --cov-report=term-missing  # with coverage
ruff check app tests wsgi.py                # lint
```

Each test runs against its own temp-file SQLite database with every migration applied fresh — the dev database is never touched.

## Database migrations

Schema changes are managed by Alembic (`ADR-0014`):

```bash
flask db migrate -m "describe the change"   # draft a revision from model changes
flask db upgrade                            # apply pending migrations
```

Review the generated revision **and** update `docs/DATABASE_SCHEMA.md` in the same change.

## CLI commands

| Command | Purpose |
|---|---|
| `flask db upgrade` / `flask db migrate` | Apply / draft schema migrations |
| `flask seed-dev` | Seed foundational content sources + builtin catalog types (idempotent) |
| `flask run` | Start the local dev server |

## Project layout

```
app/
  models/        SQLAlchemy models (one module per schema table group)
  repositories/   Query helpers; campaign-scoped ones take campaign_id first (ADR-0010)
  services/       Business logic: campaigns, content sources, catalog objects/relationships,
                  effective-catalog resolution, asset uploads, import/export
  domain/         Pure, DB-free logic: attribute validation, source-precedence resolution
  importer/       Catalog interchange document format + dry-run validator
  assets/         Image validation/re-encoding/content-addressed storage
  routes/         Flask blueprints (dashboard, campaigns, catalog, content_sources, planning)
  templates/      Jinja templates + reusable macros under _components/
  static/         CSS theme + vendored htmx/Alpine.js (no CDN at runtime)
migrations/       Alembic migration history
content/profiles/ Game profile presets (data, not code)
docs/             SDD documentation (source of truth)
tests/            pytest suite
```

## Environment variables

See `.env.example` (`SECRET_KEY`, `DATABASE_PATH`, `ASSETS_PATH`, `HOST`, `PORT`, `MAX_UPLOAD_MB`).
