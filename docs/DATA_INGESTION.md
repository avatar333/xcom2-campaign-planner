# Data Ingestion — Curated Content-Pack Importer (Phase 4)

Status: Implemented. Describes the `content/packs/<slug>/` directory format, the `flask content *` CLI, the validation/import/refresh lifecycle, and how this importer relates to the Phase 3 ad hoc interchange format. Companion to `docs/DATA_SOURCING.md` (where the facts come from) and `ARCHITECTURE.md` §5 (the format's original design).

## 1. Two importers, not one

This application has **two** distinct import paths, intentionally kept separate (`ADR-0017`):

| | Curated pack importer (this document) | Ad hoc interchange format |
|---|---|---|
| Module | `app/importer/pack_schema.py`, `app/importer/pack_validator.py`, `app/services/content_import.py` | `app/importer/document.py`, `app/importer/validator.py`, `app/services/catalog_import.py` |
| Source of data | Hand-authored, maintainer-curated JSON under `content/packs/<slug>/`, checked into the repository | Any user, exported from `/catalog/export` and re-imported via `/catalog/import` |
| Entry point | `flask content import <slug>` (CLI only) | `GET /catalog/export` / `POST /catalog/import` (UI) |
| Scope | `requires`/`extends`/`conflicts`, compound prerequisite groups, soldier stat profiles, asset manifests, provenance pinning | A flat catalog-object/relationship document, no pack dependency graph |
| Identity | `(source_id, template_key)`, resolved via `(source_slug, template_key)` pairs in the pack JSON | `(source_id, template_key)`, resolved the same way but with no cross-pack dependency concept |

Both converge on the same tables (`catalog_objects`, `catalog_relationships`, …) — there is one schema, two producers.

## 2. Pack directory layout

```
content/packs/<slug>/
  pack.json                   required: manifest
  object_types.json           optional: new catalog_object_types + attribute definitions
  relationship_types.json     optional: new catalog_relationship_types
  objects.json                required: catalog objects (array)
  relationships.json          optional: flat AND/OR relationships (array)
  prerequisite_groups.json    optional: compound nested AND/OR trees (ADR-0018)
  stat_profiles.json          optional: soldier stat profiles
  asset_manifest.json         optional: asset provenance records
  assets/                     optional: local image files, own-authored placeholders only (ADR-0008)
```

See `ARCHITECTURE.md` §5 for the full JSON shape of each file (`pack.json`, `objects.json`, `relationships.json` examples) and `app/importer/pack_schema.py` for the authoritative dataclasses/loader.

Every cross-object reference (`overrides`, `replaces`, relationship `from`/`to`, prerequisite-group members, stat-profile/asset-manifest `target`) is a `[source_slug, template_key]` pair, never a raw database id — packs are source-portable and never assume another pack's internal ids.

## 3. CLI commands (`flask content ...`, `app/cli.py`)

| Command | What it does |
|---|---|
| `flask content list` | Lists pack directories under `content/packs/`, topologically sorted by `requires` so dependencies print before dependents. |
| `flask content validate <slug>` | Loads and dry-run validates one pack (no DB writes); prints a diagnostics report (counts + warnings/errors); non-zero exit if invalid. |
| `flask content import <slug> [--force]` | Validates, then commits one pack. No-op ("already up to date") if the on-disk checksum matches what was last imported, unless `--force`. |
| `flask content import-all [--force]` | Imports every discovered pack in dependency order (`requires` topologically sorted), so e.g. `lwotc` always imports after `xcom2-base`/`wotc` regardless of directory name. |
| `flask content diff <slug>` | Reports whether the on-disk pack differs from what was last imported, and prints the pinned upstream provenance (`source_repository`/`source_ref`/`source_commit_sha`) if set. |
| `flask content reset-catalog --yes` | **Development-only.** Deletes built-in (`is_user_authored=False`) catalog rows belonging to built-in sources, and clears their `import_checksum`/`imported_at` so packs can be re-imported from a clean slate. Never touches `campaign_*` tables or user-authored rows. |

## 4. Validation (`app/importer/pack_validator.py`)

`validate_pack(document)` returns a `PackValidationReport` with `errors`/`warnings`/`is_valid` plus new/updated/retired counts. Checks performed, all read-only against the current database:

- `requires`/`extends` name an already-imported source (error if missing).
- `conflicts` names an already-imported source (warning only — enforcement that a campaign may not enable both conflicting sources happens at campaign source-enable time, `app/services/campaign_sources.py`, not at import time).
- Every object's `type` resolves to a declared (`object_types.json`) or already-existing object type.
- `overrides`/`replaces` only point at a source this pack declares in `requires`/`extends` (never an undeclared source).
- No duplicate `(source_slug, template_key)` identity within the pack itself.
- No relationship connects an object to itself.
- Every relationship/prerequisite-group endpoint resolves — either declared within this pack, or already imported under an existing source.
- Every prerequisite-group node has at least one member and a valid `logic` (`AND`/`OR`).

`import_pack()` raises `ContentPackImportError` if `report.is_valid` is `False`; nothing is written to the database for an invalid pack.

## 5. Import algorithm (`app/services/content_import.py:import_pack`)

1. Load the pack (`load_pack`) and compute its checksum (`compute_pack_checksum` — sha256 of the canonicalized JSON content of every pack file, not file mtimes).
2. If a `content_sources` row already exists for this `slug` with a matching `import_checksum` and `force` is not set: no-op, return `already_up_to_date=True`.
3. Validate (`validate_pack`); raise on any error.
4. Upsert the `content_sources` row (provenance fields `source_repository`/`source_ref`/`source_commit_sha` come straight from `pack.json`, `ADR-0019`) and its `requires`/`extends`/`conflicts` relations.
5. Upsert `catalog_object_types`/`catalog_attribute_definitions` and `catalog_relationship_types` the pack introduces (additive only — never redefines an existing type/attribute the pack doesn't own).
6. Upsert `catalog_objects` **scoped strictly to this pack's own `source_id`** — a pack can resolve references into another already-imported source (read-only), but can never write into another source's rows. This is also why a user-authored object sharing a template_key with a pack object in a *different* source never collides or gets overwritten (`tests/test_content_import.py::test_import_never_touches_user_authored_objects_in_same_source_slug`).
7. Objects that belonged to this source in a previous import but are absent from the new version are **retired** (`is_retired=True`), unless a campaign still references them (`campaign_object_state`/plan items/loadouts) — those are instead flagged `is_orphaned_upstream=True` and kept active, so an existing campaign never silently loses a resolvable object (`ARCHITECTURE.md` §5.4 step 4). Neither path ever deletes a row.
8. Upsert `catalog_relationships` (dedup via `find_duplicate` on the natural key), compound `catalog_prerequisite_groups`/`_members` trees (`ADR-0018`), and `catalog_soldier_stat_profiles`.
9. For each `asset_manifest.json` entry: if it names a `local_path` under the pack's own `assets/` directory, import those bytes now (`origin='pack_bundled'`, `import_status='imported'`); otherwise record a `pending` placeholder row capturing provenance/intent without bundling a copyrighted image (`ADR-0008`) — see the "asset-importer seam" below.
10. Commit, record the new checksum, and **bump `effective_catalog_version`** for every campaign that has this source enabled (`app/services/effective_catalog.py:bump_version_for_sources`) — this is required so an already-created campaign immediately sees the re-imported pack's changes rather than a stale cached resolution (a real bug caught and fixed by `tests/test_content_import.py::test_import_bumps_effective_catalog_version_for_affected_campaigns`).

Idempotency: re-running `flask content import <slug>` with unchanged pack content is a true no-op (no writes at all beyond the checksum comparison); re-running with changed content upserts in place, never duplicates rows (`tests/test_content_import.py` covers both).

## 6. Asset-importer seam

A pack can cite "this object's real icon exists at URL X, credited to UFOpaedia" without bundling the (possibly copyright-encumbered) image bytes in the repository. `asset_manifest.json` entries without a `local_path` become a `pending`-status `assets` row with a synthetic content hash (`app/services/asset_service.py:record_pending_asset_manifest_entry`) — inspectable from the database, attached to the object, but with no bytes on disk. A future local extraction tool (or the user, via the catalog UI's upload/fetch-from-URL actions, `FR-AST-2`/`FR-AST-4`) can fill in the real bytes for that object/role later without needing a schema change.

## 7. Refresh / re-curation workflow

Packs that cite a pinned upstream revision (`ADR-0019`: `source_repository`/`source_ref`/`source_commit_sha` in `pack.json`, currently only `lwotc`) are refreshed manually:

1. A human maintainer checks the upstream repository for changes since the pinned commit.
2. If relevant, they re-curate the affected `objects.json`/`relationships.json`/etc. entries by hand against the new commit (this app deliberately does **not** attempt automated `.ini`/`.uc` diffing — see `ADR-0018`'s "do not create a brittle one-off regex dump" guidance) and update `pack.json`'s `source_ref`/`source_commit_sha`.
3. `flask content diff <slug>` confirms the on-disk checksum changed; `flask content import <slug>` applies it in place.

`flask content validate <slug>` should be run before every import as a pre-commit sanity check; both CLI commands and a future CI job can use it as a gate.

## 8. Known gaps (tracked, not blocking)

- **Curated-pack export** (the reverse of `flask content import`, i.e. turning a running database's state for a source back into a `content/packs/<slug>/` directory) is not implemented. The Phase 3 ad hoc interchange format already covers "export my data to share it" (`GET /catalog/export`); a curated-pack-shaped export is lower priority since curated packs are meant to be hand-authored and reviewed, not round-tripped. Tracked as a `FR-SRC-8` follow-up.
- **Automated extraction from a local game/mod install** (`FR-SRC-9`) remains future work; every pack shipped in this repository was hand-authored from the authoritative sources listed in `docs/DATA_SOURCING.md`, not parsed from a user's local files.

## 9. Data conventions consumed by the Avenger Facility Planner (Phase 5, `ADR-0020`)

The planner reads catalog data only; these conventions let a pack drive it with no code change:

- **Facilities**: objects of type `facility` with attribute `buildable_in_avenger_grid` (default true) are offered in the grid. `allows_duplicates` (default false) controls whether a campaign may place the facility in more than one cell. `power_cost` (negative = generates power), `build_cost_supplies`, `build_days`, `staffing_slots` are shown when present and reported as "Not recorded" when absent — never guessed.
- **Removing a facility for a ruleset**: author an override object (`overrides` → the base facility) whose `patch.attributes` sets `buildable_in_avenger_grid: false`. The `wotc` pack does this for the vanilla-only Advanced Warfare Center (`AdvancedWarfareCenter_NotInWotC`); vanilla campaigns still see the original.
- **Room states**: objects of type `avenger_room_state`. Attributes: `clearable`, `blocks_construction` (a planned facility on this room is shown blocked until cleared; falls back to `clearable` when absent), `is_default_empty` (the state "Reset to EMPTY" applies), `gameplay_effect`, `clear_reward`. A pack may add new room states.
- **Facility prerequisites**: inbound `prerequisite` / `unlocks` / `requires_research` / `requires_facility` relationships on the facility (flat `group_key` OR/AND groups) or a compound group in `prerequisite_groups.json` (`ADR-0018`). In the shipped packs `requires_facility` and `unlocks` are authored with the *requirement as source and the dependent object as target*.
