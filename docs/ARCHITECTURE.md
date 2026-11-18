# Architecture — XCOM 2 Campaign Planner

Status: Draft v1. Normative for module boundaries, request flow, pack format and the precedence/requirement algorithms. See `DOMAIN_MODEL.md` for terminology and `DATABASE_SCHEMA.md` for the authoritative schema.

## 1. High-level shape

```
Browser (Jinja-rendered HTML + progressive JS, dark theme)
        │ HTTP (same-origin, 127.0.0.1)
        ▼
Flask app (application factory, blueprints)
  ├── routes/*            (thin: parse request → call service → render)
  ├── services/*          (use-case orchestration, transactions)
  ├── domain/*            (pure logic: requirements, effective-catalog, validation — no Flask/SQL imports)
  ├── repositories/*      (all SQL, campaign_id-mandatory for campaign-scoped tables)
  ├── importer/*          (content-pack validation + idempotent import)
  └── assets/*            (local asset storage, thumbnailing)
        │ sqlite3 (WAL, foreign_keys=ON)
        ▼
instance/app.sqlite3   +   instance/assets/**   +   content/packs/** (source files, not runtime-mutated)
```

Single Flask process, no background workers in v1 (imports/thumbnailing run synchronously within the request/CLI command; acceptable at this data scale per NFR-5).

**Phase 2 amendment**: persistence uses SQLAlchemy (`Flask-SQLAlchemy`) + Alembic (`Flask-Migrate`) instead of raw `sqlite3` + hand-written SQL migrations — see `ADR-0014` (supersedes `ADR-0007`, amends `ADR-0011`). The frontend uses htmx + Alpine.js, vendored locally, for progressive enhancement — see `ADR-0015`. The module layout and dependency direction below are otherwise unchanged; `repositories/*` now wrap a SQLAlchemy `Session` instead of raw SQL strings, and `domain/*` remains ORM-free (operates on plain dataclasses/dicts, never on model instances, to keep pure-function tests DB-free).

## 2. Module layout

```
app/
  __init__.py            create_app(), blueprint registration, config loading
  config.py               Config classes (Dev/Test/Prod), reads instance/config.py overrides
  extensions.py            db connection helper, migration runner hook
  domain/
    requirements.py        pure requirement-tree evaluation (AND/OR, "why is this unmet"); Phase 5 implements the generic evaluator, availability walk-up arrives with state tracking (ADR-0004, ADR-0018, DOMAIN_MODEL §9.1)
    dependency_graph.py    pure recursive dependency traversal: gates, statuses, path analysis, cycle detection (ADR-0021)
    avenger_grid.py        pure 3x4 grid geometry, coordinates, build-state vocabulary (ADR-0020)
    builds.py               pure build-compatibility rules (SlotSpec/ComponentFacts/validate_build) and the computed-summary arithmetic (ADR-0022)
    effective_catalog.py   source precedence resolution + caching (ADR-0013)
    attributes.py          validate JSON attributes against catalog_attribute_definitions (ADR-0012)
    plans.py                plan-item blocked/ready derivation
  repositories/
    content_sources.py, catalog_objects.py, catalog_relationships.py, assets.py
    avenger_cells.py       campaign-scoped Avenger grid cells (Phase 5)
    campaigns.py, campaign_state.py, campaign_plans.py, campaign_soldiers.py, strategy.py
    (every campaign_* function signature starts with campaign_id; see ADR-0010)
  services/
    content_import.py      orchestrates importer/* + repositories, one DB transaction per pack
    campaign_lifecycle.py   create/duplicate/archive/delete campaign (service, not raw repo calls, to keep duplication/delete logic in one audited place)
    planning.py             plan CRUD + recompute-on-write of blocked/ready
    dependency_index.py     catalog -> requirement expressions + campaign facts (ruleset/override-aware), shared by research and Avenger (ADR-0021)
    campaign_progress.py    single writer of completed/in-progress state with completion timestamp+order
    research.py             Research & Dependency Planner: browser, queue, validation, unlock path, graph payload, RecommendationProvider seam (ADR-0021)
    avenger.py              Avenger Facility Planner: grid init, configure/reset, facility options (effective catalog), assignment, build lifecycle, prerequisite + duplicate evaluation, summary (ADR-0020)
    soldiers.py             campaign soldier roster, ruleset-aware class/rank choices, default+actual stat provenance (ADR-0022)
    builds.py               generic Weapon/Armour equipment builds: slots from catalog data, compatibility validation + manual override, availability, computed summary, build assignment (ADR-0022)
  importer/
    schema.py               JSON Schema for pack.json/objects.json/relationships.json
    validator.py             dry-run diagnostics (FR-SRC-7)
    loader.py                 reads a pack directory into in-memory DTOs
  assets/
    storage.py               content-addressed filesystem store
    thumbnail.py              Pillow-based re-encode/resize (strip EXIF)
  routes/
    catalog.py, content.py, campaigns.py, planning.py, avenger.py (/campaigns/<id>/avenger/*), research.py (/campaigns/<id>/research/*), soldiers.py (/campaigns/<id>/soldiers/*, /campaigns/<id>/builds/* -- the soldiers_bp and builds_bp blueprints, ADR-0022), assets.py, api.py (JSON endpoints for progressive-enhancement JS)
  templates/            Jinja templates, see UI_UX_SPEC.md for structure
  static/               CSS (custom properties, dark theme), JS modules, SVG fallback glyphs
  cli.py                 flask db migrate / content import|validate|list / seed-dev commands

migrations/             0001_initial.sql, 0002_*.sql, ... (ADR-0007)
content/packs/          curated base/wotc/lwotc pack *data* (no binary game assets, ADR-0008)
instance/               app.sqlite3, assets/ (gitignored)
tests/
  unit/        domain/* tests, no DB
  repositories/ real-sqlite tests (tmp file per test)
  routes/       Flask test-client tests
  importer/     golden-file pack import tests
  isolation/    campaign-isolation invariant tests (ADR-0010)
```

**Dependency direction**: `routes → services → (domain, repositories) → sqlite3`. `domain/*` never imports `repositories`, Flask, or SQL — it operates on plain dataclasses/dicts passed in, which is what keeps it unit-testable without a DB and keeps "no XCOM content in Python" honest (domain code has zero awareness of what a "Magnetic Rifle" is, only of objects/types/relationships-the-shapes).

**Phase 3 amendment**: the catalog/content-source/asset/relationship/import-export seams described above are now implemented, with the following naming/scope adjustments against the original module sketch (kept here so the sketch and reality stay in sync for the next session):
- `app/domain/effective_catalog.py` exists as planned (pure resolver, `ADR-0013`); `app/services/effective_catalog.py` wraps it with DB loading + an in-process cache keyed by `(campaign_id, effective_catalog_version)`. `app/domain/requirements.py`/`app/domain/plans.py` are **not yet built** — they belong to the future planning phase and are out of scope here (per the Phase 3 brief: "do not yet build the campaign progression/recommendation engine").
- `app/repositories/` gained `catalog_objects.py`, `catalog_relationships.py`, `object_types.py`, `assets.py`, `campaign_sources.py` as planned; `campaign_state.py`/`campaign_plans.py`/`campaign_soldiers.py`/`strategy.py` remain future work (no campaign progression tracking yet).
- `app/services/` gained `content_sources.py` (CRUD + slug/validation), `catalog_objects.py` (identity, attribute validation, overlay creation), `catalog_relationships.py` (self/duplicate/cycle validation), `campaign_sources.py` (association + `conflicts` enforcement), `asset_service.py` (upload orchestration), `catalog_import.py` (the interchange-format import/export, `ADR-0017` — **not** `content_import.py`, which is reserved for the curated `content/packs/` importer described in §5 below and remains unbuilt).
- `app/importer/` gained `document.py` (the interchange document dataclasses + JSON (de)serialization) and `validator.py` (dry-run diagnostics for that document). The curated-pack-specific `schema.py`/`loader.py` described in the original sketch, and the `content/packs/` directory format in §5 below, remain future work — see `ADR-0017` for why these are deliberately two different formats serving two different jobs.
- `app/assets/storage.py` implements validation + content-addressed storage using Pillow (`ADR-0016`); a separate `thumbnail.py` was not needed since re-encoding and resizing happen in the same pass as validation.
- `routes/` gained `catalog.py` (browse/CRUD/relationships/assets/import-export) and `content_sources.py` (admin CRUD); `campaigns.py` gained source-association routes. `soldiers.py`/`api.py` remain future work.

## 3. Request flow example

`GET /campaigns/<id>/catalog?type=weapon&q=rifle`:
1. `routes/catalog.py` parses query params, calls `services` (or directly `repositories.catalog_objects.search` for a pure read) with `campaign_id` so results can be annotated with campaign state/availability.
2. `domain/effective_catalog.get(campaign_id)` returns (cached) the resolved object-id set visible to this campaign, honoring precedence (`ADR-0013`).
3. `repositories.catalog_objects.search(effective_ids, type=, q=, ...)` runs the indexed/FTS query.
4. `repositories.campaign_state.bulk_get(campaign_id, object_ids)` fetches state for the page of results.
5. `domain/requirements.availability_for(object_ids, state, relationships)` computes badges.
6. Route renders `templates/catalog/list.html` with cards (icon via `assets`, name, type badge, availability badge).

Phase 3 implements steps 1-3 (campaign-source-filtered browse, via `app/services/effective_catalog.py:effective_source_ids` + `repositories.catalog_objects.search(source_ids=...)`) and the detail/relationship/asset/import-export routes described in `IMPLEMENTATION_PLAN.md` Phase 3; steps 4-5 (campaign state + availability badges) belong to the future planning/state-tracking phase and are not yet wired into `catalog.index`.

`POST /content/import/<source_slug>` (CLI or admin route): `services/content_import.py` opens one transaction, calls `importer.loader` → `importer.validator` → repository upserts → bumps `effective_catalog_version` for every campaign that has the source enabled (or lazily invalidates on next read — see §7.3).

This curated-pack import flow (and the `services/content_import.py` module name) is **reserved for a future phase**; Phase 3's actual import/export flow is `GET /catalog/export` / `POST /catalog/import`, backed by `app/services/catalog_import.py` (document-shaped, not pack-directory-shaped — `ADR-0017`), which already bumps no `effective_catalog_version` because the interchange format has no concept of "enabled sources" (that only applies per-campaign, and importing catalog data does not itself change any campaign's source list).

## 4. Configuration

`instance/config.py` (gitignored) or environment variables: `DATABASE_PATH` (default `instance/app.sqlite3`), `ASSETS_PATH` (default `instance/assets`), `SECRET_KEY` (required for CSRF; generated on first run and persisted to `instance/secret_key` if absent), `HOST`/`PORT` (default `127.0.0.1:5000`), `MAX_UPLOAD_MB` (default 8). No external services, no API keys, by design (offline-first, NFR-4).

## 5. Content Pack format (normative, implements ADR-0005; **implemented in Phase 4** — see `app/importer/pack_schema.py`/`pack_validator.py`, `app/services/content_import.py`, `docs/DATA_INGESTION.md` for the full ingestion/CLI/refresh reference; see `ADR-0017` for Phase 3's separate, simpler interchange format at `/catalog/export`/`/catalog/import`)

```
content/packs/<slug>/
  pack.json
  object_types.json        # optional
  relationship_types.json  # optional
  objects.json
  relationships.json
  assets/                  # local image files, referenced by relative path
```


### 5.1 `pack.json`
```json
{
  "slug": "lwotc",
  "name": "Long War of the Chosen",
  "kind": "total_conversion",
  "version": "1.0.0",
  "steam_workshop_id": "1134188340",
  "requires": ["xcom2-base", "wotc"],
  "extends": ["xcom2-base", "wotc"],
  "conflicts": [],
  "license": "fact-data-only; see DATA_SOURCING.md",
  "attribution_text": "Long War 2 Studios",
  "homepage_url": "https://github.com/long-war-2/lwotc"
}
```
`requires` = hard dependency (import fails if missing/not enabled). `extends` = this pack is *permitted* to set `overrides_object_id`/`replaces_object_id` onto objects from the named source(s); import validation rejects an override pointing at a source not listed here (`DOMAIN_MODEL` invariant 5).

### 5.2 `objects.json`
```json
[
  {
    "template_key": "LaserRifle",
    "type": "weapon",
    "name": "Laser Rifle",
    "short_description": "Second-tier conventional-pattern rifle.",
    "tier": 2,
    "tags": ["primary", "conventional"],
    "attributes": {"damage_min": 4, "damage_max": 6, "crit_chance": 5, "clip_size": 6},
    "primary_asset": "assets/laser_rifle_icon.svg",
    "overrides": null
  },
  {
    "template_key": "MagnetecCoils",
    "type": "research_project",
    "name": "Advanced Magnetic Weaponry (LWotC)",
    "overrides": {"source": "wotc", "template_key": "AdvMagWeapons"},
    "patch": {"attributes": {"science_score": 55}}
  }
]
```
`overrides`/`replaces` reference the base object by `(source, template_key)`, resolved to an internal ID at import time (never a raw integer — packs are source-portable).

### 5.3 `relationships.json`
```json
[
  {"type": "prerequisite", "from": ["xcom2-base", "HybridMaterials"], "to": ["xcom2-base", "MagneticWeapons"]},
  {"type": "requires_resource", "from": ["xcom2-base", "EleriumCore"], "to": ["xcom2-base", "PlasmaRifleProject"], "amount": 2},
  {"type": "prerequisite", "from": ["xcom2-base", "AdvancedLaserWeapons"], "to": ["xcom2-base", "PlasmaRifleProject"], "group_key": "plasma-rifle-path", "group_logic": "OR"},
  {"type": "prerequisite", "from": ["xcom2-base", "AdvancedCoilguns"], "to": ["xcom2-base", "PlasmaRifleProject"], "group_key": "plasma-rifle-path", "group_logic": "OR"}
]
```
(The last two express the documented either-or path to Plasma Rifle — see `DATA_SOURCING.md`.)

### 5.4 Import algorithm (`services/content_import.py`)
1. Load + JSON-Schema-validate all files (`importer/schema.py`).
2. Resolve `requires` (error if a required source is not already imported) and `conflicts` (error if a conflicting source is imported and enabled anywhere — advisory at import, hard-checked at campaign source-enable time).
3. Compute pack checksum (sha256 of canonicalized JSON); if equal to `content_sources.import_checksum`, no-op (log "already up to date").
4. Transaction: upsert `content_sources` row; upsert `catalog_object_types`/`catalog_attribute_definitions`/`catalog_relationship_types` introduced by the pack; upsert `catalog_objects` by `(source_id, template_key)` (insert new, update changed fields on existing, resolving `overrides`/`replaces` references); mark objects present in the DB for this source but absent from the new pack version as `is_retired=1` **only if** not referenced by any `campaign_object_state`/`campaign_plan_items`/`campaign_soldier_loadouts` row, else keep active but flagged `is_orphaned_upstream` for UI warning; upsert `catalog_relationships` similarly (natural key: `(source_pack_id, relationship_type_id, source_object_id, target_object_id, group_key)`); copy new/changed files from `assets/` into asset storage (content-addressed, dedup by hash).
5. Commit; record checksum + `imported_at`; bump `effective_catalog_version` for every campaign with this source enabled (`app/services/effective_catalog.py:bump_version_for_sources`) so already-created campaigns see the re-imported changes immediately rather than a stale cached resolution.
6. `--dry-run` performs steps 1-2 and a read-only simulation of step 4 (diff report: N new, M updated, K retired, validation errors), no transaction commit.

Implementation note: the actual module names are `app/importer/pack_schema.py` (load + schema), `app/importer/pack_validator.py` (the dry-run diagnostics report — invoked via `flask content validate <slug>` rather than an `--import --dry-run` flag), and `app/services/content_import.py` (the transactional commit, `import_pack()`). The description above is the normative *algorithm*; `docs/DATA_INGESTION.md` documents the actual CLI surface and file layout in full.

## 6. Requirement / Availability resolver (`domain/requirements.py`)

Pure function(s), no I/O:

```python
def availability(
    object_id: int,
    relationships: dict[int, list[Relationship]],   # inbound edges by target_object_id
    state: dict[int, ObjectState],                   # by object_id, campaign-scoped
    visited: frozenset[int] = frozenset(),
) -> Availability:
    ...
```
Algorithm: group inbound requirement-ish relationships (`prerequisite`, `requires_resource`, `requires_facility`, `requires_research`, `class_can_equip` when evaluated for an item-on-soldier context) by `group_key` (None = its own singleton AND-group); an object is `available` iff every group has at least one satisfied member, where a relationship is "satisfied" if: for boolean kinds, the referenced object's state `status` is `completed`/`owned`/`in_progress`(configurable per kind) or `prerequisite_override=1`; for `requires_resource`, campaign `quantity` ≥ `amount`. Cycle guard via `visited`; a cycle is treated as unresolved (`unknown`) and logged — content validation (`importer/validator.py`) should flag true cycles created by a pack as a warning, since legitimate `upgrades_to`/`supersedes` chains are not requirement cycles.

"What does X unlock" is the symmetric query over outbound `unlocks`/inverse edges. "What's missing for X" is `availability(X)` with the unsatisfied leaves of the group-tree returned instead of a single verdict (used by `FR-PLN-2`'s explanation view).

## 7. Effective Catalog resolution (`domain/effective_catalog.py`, implements ADR-0013)

### 7.1 Inputs
`campaign_content_sources` (enabled + precedence order) for the campaign; the global `catalog_objects`/`overrides_object_id`/`replaces_object_id` graph.

### 7.2 Algorithm
1. `enabled_sources = ordered list of content_source_id where campaign_content_sources.enabled, sorted by precedence asc`.
2. Build identity groups: follow `overrides_object_id`/`replaces_object_id` edges transitively to cluster objects that represent "the same concept" across sources.
3. For each group, the **winner** is the group member whose `source_id` has the highest-precedence-number **among enabled sources** (i.e. the latest/most-override-y enabled source wins, matching `ADR-0013`/`DATABASE_SCHEMA.md` §3.2's "lower precedence number loads first, higher overrides"); if no member's source is enabled, the group contributes nothing to the effective catalog; if only the original (non-overriding) member's source is enabled, it wins as-is.
4. If the winner reached its slot via `overrides_object_id` (patch semantics), merge: `effective_fields = base_fields | patch` (shallow merge on top-level + `attributes` sub-merge), where `base_fields` come from the object(s) earlier in the override chain. If via `replaces_object_id`, no merge — the replacing object's own fields are used as-is.
5. Result: `{object_id -> effective_object_view}` plus a flat `visible_object_ids` set, cached as `instance/cache` row keyed `(campaign_id, effective_catalog_version)` or an in-process LRU keyed the same way; invalidated by bumping `campaigns.effective_catalog_version` whenever `campaign_content_sources` changes for that campaign or any enabled source is re-imported (import service looks up affected campaigns by `campaign_content_sources` and bumps their version).

### 7.3 Cache invalidation triggers
- `campaign_content_sources` INSERT/UPDATE/DELETE for a campaign → bump that campaign's version.
- `content_import` commit for source S → bump version for every campaign with S enabled.
- Cache is a pure function of `(campaign_id, version)`; stale entries are simply never looked up again once the version advances (no explicit eviction needed beyond an LRU bound).
- Implementation note (Phase 4 fix): the cache dict lives on `current_app.extensions`, not a bare module-level global — a module-level dict would let one Flask app instance's cached `(campaign_id, version)` key collide with an unrelated app instance's same-numbered campaign (this is exactly how every pytest test gets its own app + SQLite file), silently returning a different database's resolved catalog. Each app instance now gets its own cache, same invalidation semantics.

## 8. Asset pipeline

Upload: Flask route validates MIME via content-sniffing (not just extension), size cap (`MAX_UPLOAD_MB`), re-encodes to a canonical format/max-dimension via Pillow, computes sha256, stores at `instance/assets/<hash[:2]>/<hash>.<ext>`, inserts `assets` row (`origin='upload'`). Pack import: same storage function, `origin='pack_bundled'`. User "fetch from URL" (`FR-AST-4`): explicit user action, one-shot `requests`-free `urllib`-based fetch (still subject to the banned-tool constraints only applying to the agent's shell, not app runtime) into the same pipeline, `origin='fetched_url'`, `source_url` recorded. Serving: a dedicated `routes/assets.py` `GET /assets/<id>` sets long-cache headers (`Cache-Control: public, max-age=31536000, immutable` — content-addressed so safe) and correct `Content-Type`; never a raw filesystem path exposed to templates.

## 9. Security baseline (NFR-7)

- Flask-WTF or hand-rolled CSRF token on every mutating form/route; `SameSite=Lax` session cookie.
- Jinja autoescape on (default); never `|safe` on user-authored text fields (object descriptions, notes).
- All SQL parameterized (`sqlite3` `?` placeholders); no string-built SQL, including in the importer.
- Upload validation: magic-byte sniffing (`imghdr`/Pillow `Image.open` verify), extension allow-list, re-encode (strips embedded scripts/EXIF), filenames never taken from user input (hash-derived).
- CSP header: `default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'` (no inline, no remote).
- No `eval`/`pickle` on pack or request data; JSON only.
- Rate limiting / auth: out of scope — local single-user tool bound to `127.0.0.1` (documented non-goal, `PRODUCT_SPEC.md` §9).

## 10. Testing strategy (maps to NFR-3)

| Layer | What | Tooling |
|---|---|---|
| `tests/unit` | `domain/*` pure functions: availability, grouping, precedence resolution, attribute validation | pytest, no DB, synthetic fixtures |
| `tests/repositories` | Real SQLite (tmp file, migrations applied), CRUD + isolation of campaign-scoped queries | pytest, `tmp_path` fixture |
| `tests/importer` | Golden fixture packs (including a synthetic "override" pack) imported twice → idempotency; dry-run diagnostics | pytest |
| `tests/routes` | Flask test client, full stack against a seeded tmp DB | pytest, `app.test_client()` |
| `tests/isolation` | Two-campaign cross-mutation guard (`ADR-0010`) | pytest |
| `tests/migrations` | Apply all migrations to empty DB, diff against `DATABASE_SCHEMA.md`'s documented structure | pytest, introspects `sqlite_master` |

Coverage target ≥85% on `app/domain` + `app/services` (NFR-3). CI (or local `make test`) runs: `ruff check`, `pytest --cov`, migration-drift test.

**Phase 3 amendment**: tests live flat under `tests/` (one file per concern, e.g. `tests/test_catalog_objects.py`, `tests/test_catalog_relationships.py`, `tests/test_content_sources.py`, `tests/test_asset_storage.py`, `tests/test_effective_catalog.py`, `tests/test_catalog_import_export.py`) rather than in the `unit/`/`repositories/`/`importer/` subdirectories sketched above — the project is still small enough that per-concern files at the top level are easier to navigate than a deep subdirectory split; the *kinds* of tests described in the table (pure-domain, repository/DB, importer round-trip, route, isolation, migration) are all present, just not physically nested.
