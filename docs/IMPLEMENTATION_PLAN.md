# Implementation Plan — XCOM 2 Campaign Planner

Status: Draft v3 (revised during Phase 3 implementation — see "Plan revision" notes below). Phased delivery; each phase ends with a working, tested increment. No phase begins coding ahead of the docs it depends on.

## Plan revision note (Phase 2)

The original v1 plan numbered phases as: 0 spec, 1 schema/migrations skeleton, 2 catalog importer + read-only browse, 3 campaigns + isolation, 4 user content authoring, 5 planning, 6 soldiers/events/strategy, 7 hardening.

The Phase 2 implementation brief explicitly scoped "Phase 2" to cover: full schema + migrations, the application factory/config/service-repository skeleton, complete campaign CRUD with isolation tests, the UI shell (nav + dark theme + reusable primitives), and the *foundational* content-source schema/seed (not the importer or catalog UI). This combines v1's Phase 1 and Phase 3 (plus the non-importer slice of Phase 2) into one delivered increment, because campaign management and the UI shell are more valuable to land together early than schema-alone. Phases are renumbered below to match what was actually requested and delivered, so the plan stays an accurate map of real progress rather than a historical artifact. No scope is lost — the catalog importer and campaign-scoped catalog browsing simply move to Phase 3.

## Plan revision note (Phase 3)

The Phase 3 implementation brief scoped "Phase 3" to the full **catalog and content-source architecture**: content-source CRUD, campaign-source association/filtering, object-type-driven catalog object CRUD (including manual mod/custom objects — originally planned as a separate "Phase 4"), image upload/validation, the generic relationship system with a UI editor, a catalog browse/search/filter screen, and a first import/export interchange format with validation and conflict detection — explicitly **excluding** the curated `content/packs/` importer (`ARCHITECTURE.md` §5) and the campaign progression/planning/recommendation engine. This combines v1/v2's Phase 3 (catalog importer + browsing) and Phase 4 (user content authoring) into one delivered increment, minus the curated-pack-importer half of the old Phase 3 (which needs real curated data to be worth building and is deferred). Phases are renumbered again below; no scope is lost — the curated importer becomes its own future phase, and planning/soldiers/events/strategy keep their phase numbers shifted accordingly.

## 1. Phase 1 — Specification (complete)

Deliverables: `PRODUCT_SPEC.md`, `DOMAIN_MODEL.md`, `ARCHITECTURE.md`, `DATABASE_SCHEMA.md`, `UI_UX_SPEC.md`, `DATA_SOURCING.md`, `adr/*`, this plan. No application code, migrations or seed packs were created in Phase 1.

## 2. Phase 2 — Foundation: schema, campaigns, UI shell (complete)

Goal: a running Flask app, full documented schema applied via migrations, complete campaign CRUD with proven isolation, the persistent-nav dark UI shell, and the minimum content-source foundation — with Catalog/Planning as polished placeholders.

- `app/` package: application factory (`create_app`), environment-separated config (`Development`/`Testing`/`Production`), `extensions.py` (SQLAlchemy, Migrate, CSRF), `app/models/*` (every table in `DATABASE_SCHEMA.md` as a SQLAlchemy model — see `ADR-0014`), `app/repositories/*` (campaign_id-mandatory query helpers, `ADR-0010`), `app/services/*` (campaign lifecycle, content-source seeding).
- Alembic migration history under `migrations/` producing the exact schema in `DATABASE_SCHEMA.md` §1-5 (catalog, assets, campaign, strategy, minus `schema_migrations` which Alembic replaces — `ADR-0014`).
- `flask seed-dev` CLI command seeding the four foundational `content_sources` (`xcom2-base`, `wotc`, `lwotc`, `user-local`) and the builtin `catalog_object_types`/`catalog_relationship_types` rows (`DATABASE_SCHEMA.md` §9) — no XCOM game objects.
- Campaign CRUD: list, create (modal, with game-profile picker seeding `campaign_content_sources`), detail/dashboard, edit, delete (confirmation), fully isolated per `ADR-0010`.
- UI shell: persistent left nav (Dashboard, Campaigns, Catalog\*, Planning\*, with structural room for Research/Engineering/Avenger/Soldiers/Loadouts/Inventory/Strategy — \*placeholder screens), campaign switcher making the active campaign visually obvious, dark XCOM-inspired theme (`UI_UX_SPEC.md` §1) implemented as hand-written CSS custom properties, reusable primitives (card, badge, object tile, modal, form controls, table/list, empty state, alert/toast, section header).
- HTMX + Alpine.js, vendored locally, for modal workflows and the campaign switcher (`ADR-0015`).
- Tests: app-factory smoke test, migration/schema test, campaign CRUD (service + route), validation, campaign isolation (the canonical two-campaign test from `ADR-0010`), route smoke tests for every nav destination.

**Definition of done**: `flask db upgrade` on an empty SQLite file produces the full documented schema; `flask seed-dev` populates foundational reference data only; campaign CRUD works end-to-end through the UI and is covered by automated tests; two independent campaigns demonstrably do not affect each other or the catalog; Catalog/Planning render polished "coming in Phase 3" states reachable from the nav; full test suite passes.

## 3. Phase 3 — Catalog and content-source architecture (complete)

Goal: a fully data-driven catalog system — content sources, object types, catalog objects, images, relationships — manageable end-to-end through the UI, with a campaign able to filter the catalog by its enabled sources, and a safe import/export path for catalog data. No campaign progression/state tracking, no curated XCOM/LWotC data, no planning engine.

- `app/repositories/{content_sources,object_types,catalog_objects,catalog_relationships,assets,campaign_sources}.py` and `app/services/{content_sources,catalog_objects,catalog_relationships,campaign_sources,effective_catalog,asset_service,catalog_import}.py` implementing FR-SRC-1/3/4/5/6/7/8, FR-CAT-1..8, FR-AST-1/2/3/5/6, FR-CMP-2/3.
- `app/domain/{attributes,effective_catalog}.py`: pure, DB-free attribute validation (`ADR-0012`) and source-precedence/override-merge resolution (`ADR-0013`) — `app/domain/requirements.py`/`plans.py` remain future work (planning phase).
- `app/assets/storage.py`: Pillow-based image validation/re-encoding/content-addressed storage (`ADR-0016`), wired to upload routes with size/format/content-sniffing validation (`FR-AST-2`, `ARCHITECTURE.md` §9).
- `app/importer/{document,validator}.py` + `app/services/catalog_import.py`: a lean, versioned JSON interchange format (`ADR-0017`) for catalog export/import, always dry-run-validated with a conflict report before commit, never silently overwriting existing records.
- Routes: `routes/content_sources.py` (admin CRUD), `routes/catalog.py` (browse/search/filter/paginate, object CRUD incl. manual mod/custom objects, image upload, relationship add/remove, object picker JSON endpoint, export/import), `routes/campaigns.py` gained source-association endpoints (add/remove/enable/disable).
- Templates: image-forward catalog grid + compact list view with a type/source filter rail (`UI_UX_SPEC.md` §3.2 realized), object detail page (image, attributes, provenance, inbound/outbound relationships, overlay action), relationship add modal with an Alpine-driven image+name object picker, campaign source-management page, import/export UI with a validation-report-before-commit flow.
- Campaign-scoped catalog filtering: `catalog.index` restricts results to the active campaign's *enabled* sources via `app/services/effective_catalog.py:effective_source_ids`, satisfying the acceptance criterion "switch campaigns and see catalog content filtered by applicable content sources" without yet needing the full override-merge view (no two sources currently define the same object, since no curated data exists — the override/patch *mechanism* is proven via `create_overlay`, exercised in tests, ready for when curated data introduces real overrides).
- Tests: content-source CRUD, campaign-source association + `conflicts` enforcement, catalog-object CRUD, custom/arbitrary object types, mod/custom objects end-to-end, duplicate-identity rules `(source_id, template_key)`/`(source_id, slug)`, relationship CRUD + self/duplicate/cycle validation, campaign catalog filtering, image upload validation (valid/invalid/oversized/polyglot), import dry-run validation + conflict detection, export/import round trip (idempotent, no duplication), campaign isolation re-proven against real catalog objects.

**Definition of done**: all 10 acceptance-criteria steps in the Phase 3 brief work end-to-end (verified both by the automated suite and a manual walkthrough during implementation); full test suite passes; SDD updated with `ADR-0016`/`ADR-0017` and the `ADR-0004` cycle-policy addendum.

## 4. Phase 4 — Curated content-pack importer

Goal: import the `content/packs/<slug>/` curated pack format (`ARCHITECTURE.md` §5) — the format reserved but not yet built — once real base/WotC/LWotC data is ready to be authored (`DATA_SOURCING.md`). Produces `app/importer/{schema,loader}.py` and `app/services/content_import.py` (distinct from Phase 3's `catalog_import.py`, `ADR-0017`), `requires`/`extends`/`conflicts` pack-dependency resolution, and the `default_asset_id` per-type fallback glyphs (`FR-AST-5` upgrade). Also closes the two Phase 3 gaps noted in `PRODUCT_SPEC.md`: FR-AST-4 ("fetch from URL") and drag-and-drop source-precedence reordering.

**Status: done.** Delivered: `app/importer/pack_schema.py` (pack loader, `PackDocument` dataclasses, checksum), `app/importer/pack_validator.py` (dry-run diagnostics), `app/services/content_import.py` (transactional importer: object/relationship/compound-prerequisite-group/stat-profile/asset-manifest upsert, retire-vs-orphan on removal, checksum idempotency, `effective_catalog_version` bump on every import so already-created campaigns see re-imported changes), `app/repositories/prerequisite_groups.py` + `soldier_stat_profiles.py`, `flask content {list,validate,import,import-all,diff,reset-catalog}` (`app/cli.py`), ADR-0018 (compound AND/OR prerequisite groups) and ADR-0019 (content-source revision pinning), the DB migration adding `catalog_prerequisite_groups`/`_members`, `catalog_soldier_stat_profiles`, and provenance/asset-manifest columns, and `docs/DATA_INGESTION.md` (the full ingestion/validation/refresh reference). Three real curated packs ship under `content/packs/`: `xcom2-base` (91 objects), `wotc` (6 objects), `lwotc` (12 objects, including a genuine `PlasmaRifle_LWOTC` override of the base Plasma Rifle and a compound OR-group prerequisite), authored from UFOpaedia/SDK research and the real pinned LWotC GitHub repository (`v1.1.0`). Catalog UI filters extended with tier, class-compatibility and attribute-value (research category) filter rails (`app/repositories/catalog_objects.py:search`, `app/templates/catalog/index.html`). FR-AST-4 ("fetch from URL") implemented end-to-end with an SSRF guard (`app/services/asset_service.py:fetch_image_for_object`); drag-and-drop source-precedence reordering implemented in `app/templates/campaigns/sources.html` (Alpine.js, posts to the pre-existing `reorder_sources` route). Per-type fallback glyphs implemented for every seeded object type. A real production bug was found and fixed while building the Phase 4 acceptance test: the effective-catalog cache was a bare module-level dict rather than scoped to the Flask app instance, and `content_import.py` never bumped `effective_catalog_version` after a pack re-import — both fixed, both covered by regression tests.

**Definition of done**: Success criterion #1 from `PRODUCT_SPEC.md` §7 (two campaigns, different sources, independent state) passes against *real* imported catalog objects with at least one genuine cross-source override exercised by the effective-catalog resolver. **Met** — see `tests/test_phase4_acceptance.py` (imports the real `content/packs/` data, creates a WotC-standard and an LWotC campaign, asserts the WotC campaign resolves the base `PlasmaRifle` while the LWotC campaign resolves `PlasmaRifle_LWOTC` with its merged patch fields, and asserts fully independent `campaign_object_state` between two campaigns).

## 5. Phase 5 — Campaign state & progression tracking

Goal: `campaign_object_state`, `app/domain/requirements.py` (availability / "what's missing", `ARCHITECTURE.md` §6), campaign-state quick-edit on catalog object cards/detail, the object detail page's "what does this unlock" view.

**Definition of done**: Success criterion #2 passes.

## 6. Phase 6 — Planning

Goal: replace the Planning nav placeholder with plans/plan items, blocked/ready derivation, requirement-tree drawer, dashboard priorities/blocked panels.

**Definition of done**: Success criterion #3 passes.

## 7. Phase 7 — Soldiers, events, strategy profiles

Goal: Soldiers/Loadouts nav sections gain real screens; Events log; strategy profiles end-to-end.

**Definition of done**: all FR-* marked **M** are implemented and tested.

## 8. Phase 8 — Platform hardening

Goal: backup/export, pack re-import regression, security/accessibility pass, coverage check.

**Definition of done**: all 5 success criteria in `PRODUCT_SPEC.md` §7 pass as automated tests; NFR checklist signed off.

## 9. Phase ordering rationale

Foundation (schema+campaigns+shell) before the catalog architecture keeps the highest-risk structural guarantee — campaign isolation — proven early and in front of a real UI, before any game-content complexity (content sources, relationships, precedence) is layered on top. The catalog/content-source architecture (Phase 3) comes before the curated importer (Phase 4) because the manual-authoring path (content sources, object CRUD, relationships, images, import/export) is what proves the schema and UI are genuinely data-driven — if a user can build an equivalent of curated content by hand through the UI alone, a future importer is just another producer of the same rows, not a new code path. State tracking (Phase 5) before planning (Phase 6) because plans need object state to derive blocked/ready against. Each phase's "Definition of done" stays independently testable so a new session can verify prior phases are genuinely complete before continuing.

## 10. Out of scope for all phases above (tracked as future `F`-priority items)

Save-file parsing, local game-install extraction (`FR-SRC-9`), facility grid-layout planning (`FR-PLN-7`), online Workshop browsing, light theme, mobile navigation — see `PRODUCT_SPEC.md` §9 and the `F`-priority rows in §4.
