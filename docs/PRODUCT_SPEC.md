# Product Specification — XCOM 2 Campaign Planner

Status: Draft v1 (Phase 1 – specification). Normative unless marked *(non-normative)*.
Terminology: see `DOMAIN_MODEL.md` §1 (glossary). Requirement IDs (`FR-*`, `NFR-*`) are referenced by the implementation plan and by tests.

## 1. Purpose

A locally-run web application that helps an XCOM 2 player **plan** and **track** one or more campaigns. It answers "where am I, what is available, what is blocking me, and what should I do next" for vanilla XCOM 2, War of the Chosen (WotC), Long War of the Chosen (LWotC) and arbitrary community mods.

It is a **planner and tracker**, not a save-game editor and not a game-rules simulator. It never reads or modifies game save files in the scope of this spec.

## 2. Users and deployment

- Primary persona: a single PC player running campaigns, possibly several at once, possibly with different mod sets.
- Deployment: single-user, local. `flask run` on the player's machine, SQLite file on disk. No accounts/authentication in v1 (see §9 non-goals). The server binds to `127.0.0.1` by default.
- The app must work **fully offline** at runtime (no CDN, no hot-linked images; see `ADR-0006`, `ADR-0008`).

## 3. Core principles

1. **Catalog ≠ campaign state.** Game content is data (the *catalog*), loaded from *content packs*. Campaign state references catalog objects but never copies or mutates them. (`ADR-0002`)
2. **No XCOM content in Python.** Python code contains no object names, template IDs, costs or relationships. Everything game-specific lives in pack data files and the database.
3. **Extensible by data.** New object categories, attributes, relationship kinds and whole mods can be added without a schema migration or code change. (`ADR-0003`, `ADR-0004`)
4. **Facts vs. advice.** Game rules/relationships are *facts* with provenance. Recommendations are *opinions* in optional strategy profiles. They are stored, queried and displayed separately. (`ADR-0009`)
5. **Provenance everywhere.** Every object, relationship, overlay and asset records where it came from and whether it is imported or user-authored.
6. **Isolation.** Campaigns are fully independent; editing/deleting one cannot affect another or the catalog. (`ADR-0010`)
7. **Spec first.** Behaviour changes start with a docs change. (`ADR-0001`)

## 4. Functional requirements

Priority: **M** = must (v1), **S** = should (v1 if capacity), **F** = future (data model must not preclude it).

### 4.1 Content sources / packs

| ID | Req | Pri |
|---|---|---|
| FR-SRC-1 | The system has first-class *content sources* (base game, DLC, total conversion, Workshop mod, user content), each with name, slug, kind, version, optional Steam Workshop ID, description, attribution/licence. | M — **implemented** Phase 3: full CRUD at `/content/sources`, `app/services/content_sources.py`. |
| FR-SRC-2 | Sources are loaded from *content pack* files (format in `ARCHITECTURE.md` §6) by an idempotent importer; re-importing a newer version updates imported rows in place without losing campaign state. | M — **not yet implemented**: the curated `content/packs/` importer (`ARCHITECTURE.md` §5.4) is deferred to the phase that populates curated XCOM/LWotC data. Phase 3 ships a *different*, simpler interchange format for ad hoc UI import/export instead (`ADR-0017`, FR-SRC-7/8 below). |
| FR-SRC-3 | A source may declare dependencies on other sources (`requires`, `conflicts`, `extends`). | M — **implemented**: `content_source_relations` table + `app/repositories/content_sources.py:set_relations/list_relations`; `conflicts` is enforced at campaign source-enable time (`app/services/campaign_sources.py`). No UI yet to author `requires`/`extends` relations (no curated pack importer exists yet to need it). |
| FR-SRC-4 | A source can introduce new object types, attribute definitions and relationship types via its pack. No migration is needed. | M — **data model implemented and proven** (`app/repositories/object_types.py`, `app/services/catalog_import.py` creates new object types from an interchange document); the *pack*-specific path (`ARCHITECTURE.md` §5.1 `object_types.json`) awaits the curated importer. |
| FR-SRC-5 | A source can override/replace/patch objects and relationships of other sources (precedence model, `ARCHITECTURE.md` §5). | M — **domain logic implemented and unit-tested** (`app/domain/effective_catalog.py`, `app/services/catalog_objects.py:create_overlay`); exercised end-to-end via the catalog UI's "Edit (creates overlay)" action on built-in objects. |
| FR-SRC-6 | Users can create *user sources* (e.g. "My Workshop mod X") to hold manually authored content. | M — **implemented**: any non-builtin `content_sources` row works as a user source; `get_or_create_user_source()` resolves the seeded `user-local` default. |
| FR-SRC-7 | Packs can be validated (dry-run) with a human-readable diagnostics report before import. | S — **implemented** for the Phase 3 catalog interchange format (`app/importer/validator.py`, `/catalog/import` UI), not yet for the curated pack format (which does not exist yet). |
| FR-SRC-8 | Pack export of a user source to the pack format (for sharing/backup). | S — **implemented** for the Phase 3 catalog interchange format (`GET /catalog/export`, `app/services/catalog_import.py:export_document`), not the curated pack format. |
| FR-SRC-9 | Extraction of pack data from the user's local game/mod install (INI/template parsing). | F — not started (future). |

### 4.2 Catalog

| ID | Req | Pri |
|---|---|---|
| FR-CAT-1 | Catalog objects have a stable internal ID, type, source, name, short/long description, optional game template name/class, tags, images, notes, and typed free-form attributes. | M — **implemented**: `app/services/catalog_objects.py`, `/catalog/objects/new`. |
| FR-CAT-2 | Object types are data and extensible (see `DOMAIN_MODEL.md` §4 for the seed list). | M — **implemented**: seeded via `flask seed-dev` (Phase 2), browsable/filterable in the Catalog UI (Phase 3); no UI yet to *author* a brand-new object type (only via the interchange importer) — tracked as remaining work. |
| FR-CAT-3 | Generic typed relationships between any two objects with provenance, optional amounts and alternative (OR) groups. | M — **implemented**: `app/services/catalog_relationships.py`, relationship editor on the object detail page. |
| FR-CAT-4 | Browse/search/filter the catalog by type, source, tag, attribute, text (FTS). Results shown as cards with icon + name. | M — **implemented** with one documented gap: text search uses SQL `LIKE` (`app/repositories/catalog_objects.py:search`), not the SQLite FTS5 virtual table described in `DATABASE_SCHEMA.md` §1.5 — acceptable at the current (near-zero) catalog size; FTS5 is a drop-in follow-up once real data volume justifies it. Attribute-value filtering is not yet exposed in the UI. |
| FR-CAT-5 | Object detail view: description, attributes, images, provenance, inbound/outbound relationships, "what does this unlock", "what does this require". | M — **implemented** except the derived "what does this unlock"/"what am I missing" *availability* computation, which depends on campaign state and is explicitly out of scope for this phase (belongs to the future planning engine, `FR-PLN-2`). The detail page shows the raw relationship graph both directions. |
| FR-CAT-6 | Built-in/imported objects are read-only. Users change them by creating an **overlay** in a user source; the original row is untouched. | M — **implemented**: `/catalog/objects/<id>/overlay`. |
| FR-CAT-7 | Users can create objects of any type in a user source, attach images, set attributes (guided by the type's attribute definitions) and relationships to any existing object. | M — **implemented** end-to-end. |
| FR-CAT-8 | Objects removed upstream are *retired*, not deleted, if any campaign state references them. | M — **implemented**: `app/services/catalog_objects.py:retire_or_delete`. |

### 4.3 Assets

| ID | Req | Pri |
|---|---|---|
| FR-AST-1 | Objects can have one primary image and additional images with roles (icon, portrait, banner, gallery). | M — **implemented**: `app/repositories/assets.py`, `object_assets` join table. |
| FR-AST-2 | Upload local images (PNG/JPEG/WebP/GIF, size-limited, validated, re-encoded thumbnails). | M — **implemented**: `app/assets/storage.py` (`ADR-0016`). |
| FR-AST-3 | Import assets from a local folder/pack archive; record origin, source URL, licence, attribution, redistribution flag. | M — **implemented** for direct upload (`origin='upload'`); the "pack archive" bulk-import path awaits the curated pack importer (FR-SRC-2). |
| FR-AST-4 | A user-initiated "fetch from URL" action downloads and stores the image locally; the runtime UI only ever serves local files. | S — **not yet implemented**; the upload form only accepts a `source_url` as informational metadata today, it never fetches from it. Remaining work, see end-of-phase report. |
| FR-AST-5 | Every type has a default fallback image/glyph; every object resolves to *some* image. | M — **implemented** via one shared generic glyph (`app/services/asset_service.py:fallback_glyph_for_type`) rather than per-type glyphs; `catalog_object_types.default_asset_id` exists in the schema for a future per-type glyph upgrade. |
| FR-AST-6 | The repository contains no game assets whose redistribution rights are unclear; bundled assets must have `redistribution = allowed` and attribution. | M — holds structurally: no binary assets are bundled in the repository; uploaded assets default to `redistribution_allowed=0`. |

### 4.4 Campaigns

| ID | Req | Pri |
|---|---|---|
| FR-CMP-1 | Create/rename/duplicate/archive/delete campaigns: name, game profile (e.g. "WotC vanilla", "LWotC"), difficulty, iron-man flag, notes, timestamps. | M |
| FR-CMP-2 | A campaign enables an ordered set of content sources (order = precedence). Creating from a *game profile* pre-selects the profile's default sources. | M — **implemented**: `/campaigns/<id>/sources` (add/remove/enable/disable); explicit drag-and-drop *reordering* UI is deferred (precedence currently follows attach order), see end-of-phase report. |
| FR-CMP-3 | Enabling/disabling/re-ordering sources after creation is allowed and never deletes campaign state; state for objects no longer visible is retained and flagged "inactive". | M — add/remove/enable/disable **implemented** and proven to never touch `campaign_object_state` (no such rows exist to touch yet in this phase, since no progression tracking exists until a future phase — the structural guarantee, FK `RESTRICT`, is already in place and tested by `tests/test_campaign_isolation.py`). |
| FR-CMP-4 | Duplicating a campaign copies its state/plans/soldiers (deep copy) into a new independent campaign. | S |
| FR-CMP-5 | Deleting a campaign deletes only that campaign's data. | M |
| FR-CMP-6 | A campaign dashboard shows progression summary, current priorities, blocked plan items and recent events. | M |

### 4.5 Campaign state & progression

| ID | Req | Pri |
|---|---|---|
| FR-STA-1 | Per (campaign, object): status, quantity, notes, type-specific state data, timestamps; independent between campaigns. | M |
| FR-STA-2 | Derived **availability** (available / blocked-by / satisfied) is computed from catalog relationships + campaign state; it is never stored as fact. | M |
| FR-STA-3 | Users may manually override a prerequisite check on a state row ("mod/other effect granted this"). | M |
| FR-STA-4 | Append-only campaign event log (research completed, item acquired, facility built…) with optional in-game date label. | M |
| FR-STA-5 | Inventory is expressed as quantity state on item/resource objects; resources have quantities. | M |

### 4.6 Planning

| ID | Req | Pri |
|---|---|---|
| FR-PLN-1 | Campaign-scoped plans containing plan items (research X, build facility Y, acquire Z, equip W on soldier S, learn ability A…) with priority, order, rationale, notes. | M |
| FR-PLN-2 | "What does this unlock?" and "What am I missing?" explanations: recursive requirement tree with satisfied/unsatisfied leaves, honouring AND/OR groups and resource amounts. | M |
| FR-PLN-3 | Plan items are flagged **blocked** (with reasons) or **ready** automatically. Plan-item-to-plan-item manual dependencies are supported. | M |
| FR-PLN-4 | "Next up" list: ready items ordered by user priority, optionally re-ranked by an enabled strategy profile. | S |
| FR-PLN-5 | Strategy profiles/recommendations: optional, attributable, toggled per campaign, displayed visually distinct from facts. | S (model M) |
| FR-PLN-6 | Soldiers per campaign with class, rank, status; per-soldier *current* and *planned* equipment/ability builds; class-can-equip validation as a warning (not a hard block). | S (model M) |
| FR-PLN-7 | Facility layout planning (grid position on the Avenger). | F |

### 4.7 Platform

| ID | Req | Pri |
|---|---|---|
| FR-PLT-1 | JSON backup/export of a campaign and restore into a new campaign. | S |
| FR-PLT-2 | Whole-DB backup (SQLite online backup) action. | S |
| FR-PLT-3 | CLI commands: `flask db upgrade` (apply migrations), `flask content import|validate|list`, `flask seed-dev`. (`ADR-0014`: Alembic/Flask-Migrate conventions — `flask db migrate` drafts a new revision, `flask db upgrade` applies pending ones.) | M |

## 5. Non-functional requirements

| ID | Req |
|---|---|
| NFR-1 | Python ≥ 3.12, Flask, SQLite via SQLAlchemy ORM (`ADR-0014`). Server-side rendering; JS (htmx/Alpine, `ADR-0015`) only for progressive enhancement. |
| NFR-2 | All schema changes via forward-only Alembic migrations (`ADR-0014`, supersedes the hand-written-SQL approach of `ADR-0007`). A test applies all migrations to an empty DB and compares with the documented schema. |
| NFR-3 | Automated tests required: unit (pure logic), repository/integration (real SQLite, in-memory/tmp), route tests (Flask test client), importer golden tests, and campaign-isolation tests. Target ≥ 85% line coverage on `app/domain` and `app/services`. |
| NFR-4 | Works offline; no runtime network calls except explicit user-triggered asset fetch (FR-AST-4). |
| NFR-5 | Catalog browse/search ≤ 150 ms for catalogs up to 20 000 objects/100 000 relationships on a laptop (indexes + FTS5). Effective-catalog resolution for a campaign ≤ 500 ms cold, cached afterwards. |
| NFR-6 | Accessibility baseline: keyboard-navigable nav/modals, focus trapping, WCAG AA contrast in the dark theme, `alt` text from object names. |
| NFR-7 | Security baseline: CSRF protection on mutating routes, strict upload validation (type sniffing, size cap, path-safe storage names), Jinja autoescape, parameterised SQL only, CSP without inline scripts. |
| NFR-8 | Deterministic, idempotent imports; pack checksum recorded. |
| NFR-9 | Dark theme only in v1, but all colours via CSS custom properties so a second theme is purely additive. |

## 6. UX summary

Detailed in `UI_UX_SPEC.md`. Persistent left navigation; card-based browsing with icon + name; drawers for object detail and quick-edit; modals for confirmations and short forms; campaign switcher always visible.

## 7. Success criteria for v1

1. Create two campaigns (WotC vanilla and LWotC), enable different sources, mark "Magnetic Weapons" researched in A only; B shows it untouched.
2. Create a custom mod object (e.g. a Workshop weapon), give it an image and a prerequisite relationship to an existing research object, and see it in the planner with correct blocked/ready status.
3. Add a plan with three items, see which is blocked and why, complete the blocker, see the plan update.
4. Import a new version of a pack and keep all campaign state.
5. Delete a campaign; verify via tests that all other tables' rows are unchanged.

## 8. Assumptions (resolved ambiguities)

| # | Ambiguity | Resolution |
|---|---|---|
| A1 | Auth/multi-user? | Single local user; schema keeps `campaigns` free of user columns but all access goes through a service layer so an owner column can be added by migration later. |
| A2 | Where does factual game data come from? | Authored/curated *pack files* checked into `content/packs/` (facts only: names, template IDs, relationships, costs, short original-wording descriptions) plus user-local extraction later. Verified against SDK source/INI and UFOpaedia; see `DATA_SOURCING.md`. |
| A3 | Pack format? | JSON (stdlib, no extra dependency), schema-validated; YAML may be added later as an alternative reader. |
| A4 | Localisation? | English only in v1; `name`/`description` stay on the object; an `catalog_object_i18n` table can be added later (non-destructive). |
| A5 | Are costs/stats normalized columns? | Costs are `requires-resource` relationships (amount column). Stats and other type-specific numbers live in typed `attributes` JSON validated against `catalog_attribute_definitions`. |
| A6 | Does the app model soldiers individually? | Yes, lightweight (name, class, rank, status, builds). No stat simulation. |
| A7 | Time model? | No simulated calendar. Events carry a free-form `in_game_label` (e.g. "Month 3, Day 12") and real timestamps. |

## 9. Non-goals (v1)

Save-file parsing/editing; combat/tactical simulation; multi-user accounts or cloud sync; automatic redistribution of game assets; online Workshop browsing; light theme; mobile-first design.
