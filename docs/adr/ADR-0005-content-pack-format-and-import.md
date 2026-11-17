# ADR-0005: JSON content-pack format with idempotent, checksum-tracked import

## Status
Accepted

## Context
Game content must be loaded without touching Python code, by the application's maintainer (curated base-game/WotC/LWotC packs) and eventually by end users (Workshop mods, user content). Re-importing an updated pack (e.g. a mod author bumps a version) must not clobber per-campaign state or duplicate rows, and must be auditable.

## Decision
A content pack is a directory `content/packs/<slug>/` containing:
- `pack.json` — manifest: `slug`, `name`, `kind` (`base`, `dlc`, `total_conversion`, `workshop_mod`, `user`), `version`, `steam_workshop_id?`, `requires`/`conflicts`/`extends` (other pack slugs), `license`/`attribution`.
- `object_types.json` (optional) — new `catalog_object_types` + `catalog_attribute_definitions` rows this pack introduces.
- `relationship_types.json` (optional) — new `catalog_relationship_types`.
- `objects.json` — array of catalog objects, each keyed by a pack-local `template_key` (stable across versions; need not be globally unique, see ADR-0004/DOMAIN_MODEL provenance rules).
- `relationships.json` — array referencing objects by `(pack_slug, template_key)` pairs (resolved to internal IDs at import time), or to objects in a *required* pack.
- `assets/` — local image files referenced by relative path from `objects.json`.

Import (`flask content import <slug>`) is a transaction per pack: resolve `requires`, upsert `content_sources`, upsert object types/attribute defs/relationship types, upsert objects by `(source_id, template_key)` unique key (insert or update-in-place, never delete unless the object disappeared from the pack **and** no campaign references it — otherwise it is *retired*, flag `is_retired=1`, kept for FK integrity), upsert relationships the same way, copy new/changed assets. A SHA-256 checksum of the normalized pack content is stored on `content_sources.import_checksum`; re-import with an unchanged checksum is a no-op (idempotent). A `--dry-run` validates and reports without writing (FR-SRC-7).

## Consequences
- Mods/users add content by dropping a pack directory and running the importer — no code change, satisfying the "no schema/code change per mod" requirement.
- Versioning/upgrades preserve campaign state because `campaign_object_state` references internal catalog IDs that survive updates (same `(source_id, template_key)` ⇒ same internal row, fields updated in place).
- Deterministic imports (NFR-8) make golden-file importer tests practical: import a fixture pack twice, assert row counts/checksums are stable.

## Alternatives considered
- YAML packs: deferred — JSON needs no extra dependency (`stdlib json`) and is trivial to validate with JSON Schema; a YAML reader can be layered on later without changing the model (note in PRODUCT_SPEC A3).
- Importing directly from compiled game/mod files (INI/UPK) in this phase: deferred to `FR-SRC-9` (future) — valuable for authoring *future* packs from the SDK/INI (see `DATA_SOURCING.md`) but not required for the app to function, and adds a large parsing surface better handled by a separate offline tool that *produces* JSON packs.
