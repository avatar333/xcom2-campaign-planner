# Database Schema — XCOM 2 Campaign Planner

Status: Draft v1. This is the **authoritative** schema; the SQLAlchemy models under `app/models/` and the Alembic initial revision must match it exactly (verified by `tests/test_migrations.py`, `ADR-0014`, which supersedes the raw-SQL approach of `ADR-0007`). Engine: SQLite, `PRAGMA foreign_keys = ON;`, `PRAGMA journal_mode = WAL;`. All timestamps are `TEXT` ISO-8601 UTC (`strftime('%Y-%m-%dT%H:%M:%fZ','now')`), all booleans are `INTEGER` 0/1.

Conventions: every table has an integer `id` surrogate primary key unless noted. Every FK is declared `NOT NULL` unless marked nullable. `ON DELETE` behaviour is specified per FK — see §7 for the campaign-isolation rationale (`ADR-0010`) and §8 for precedence (`ADR-0013`).

## 1. Catalog tables (content-agnostic, never reference campaigns)

### 1.1 `content_sources`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| slug | TEXT NOT NULL UNIQUE | e.g. `xcom2-base`, `lwotc`, `workshop-1134188340` |
| name | TEXT NOT NULL | |
| kind | TEXT NOT NULL | CHECK IN (`base`,`dlc`,`total_conversion`,`workshop_mod`,`user`) |
| version | TEXT NOT NULL | semver-ish string, free form |
| steam_workshop_id | TEXT NULL | |
| description | TEXT NULL | |
| license | TEXT NULL | |
| attribution_text | TEXT NULL | |
| homepage_url | TEXT NULL | |
| is_enabled_by_default | INTEGER NOT NULL DEFAULT 0 | used when prefilling a game profile |
| is_builtin | INTEGER NOT NULL DEFAULT 0 | curated vs. user-added |
| import_checksum | TEXT NULL | sha256 of last imported pack content |
| imported_at | TEXT NULL | |
| source_repository | TEXT NULL | Phase 4, `ADR-0019`: e.g. `github.com/long-war-2/lwotc`, for sources curated from an external repo |
| source_ref | TEXT NULL | the pinned branch/tag/release, e.g. `v1.1.0` |
| source_commit_sha | TEXT NULL | the pinned immutable commit SHA at `source_ref`, so a later refresh can diff what changed |
| created_at | TEXT NOT NULL | |
| updated_at | TEXT NOT NULL | |

Indexes: `UNIQUE(slug)` (implicit via column constraint).

### 1.2 `content_source_relations`
Expresses `requires` / `conflicts` / `extends` between sources (`ADR-0005`, `ADR-0013`).

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| source_id | INTEGER NOT NULL FK → content_sources.id ON DELETE CASCADE | the declaring pack |
| related_source_id | INTEGER NOT NULL FK → content_sources.id ON DELETE RESTRICT | the referenced pack |
| relation | TEXT NOT NULL | CHECK IN (`requires`,`conflicts`,`extends`) |

Indexes: `UNIQUE(source_id, related_source_id, relation)`; `INDEX(related_source_id)`.
Delete behaviour: deleting a source cascades its own declared relations; a source that others depend on (`related_source_id`) cannot be deleted while referenced (`RESTRICT`) — app enforces "retire, don't delete" for sources with any imported objects.

### 1.3 `catalog_object_types`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| slug | TEXT NOT NULL UNIQUE | e.g. `weapon`, `research_project`, `covert_action` |
| name | TEXT NOT NULL | |
| description | TEXT NULL | |
| default_asset_id | INTEGER NULL FK → assets.id ON DELETE SET NULL | fallback glyph; nullable only until seeded, app enforces non-null in practice |
| sort_order | INTEGER NOT NULL DEFAULT 0 | |
| is_builtin | INTEGER NOT NULL DEFAULT 0 | |
| source_id | INTEGER NULL FK → content_sources.id ON DELETE CASCADE | which pack introduced this type (NULL = app-seeded) |
| created_at | TEXT NOT NULL | |

### 1.4 `catalog_attribute_definitions`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| object_type_id | INTEGER NOT NULL FK → catalog_object_types.id ON DELETE CASCADE | |
| key | TEXT NOT NULL | e.g. `damage_min` |
| data_type | TEXT NOT NULL | CHECK IN (`string`,`integer`,`number`,`boolean`,`enum`,`text`) |
| enum_values | TEXT NULL | JSON array, required if data_type='enum' |
| unit | TEXT NULL | e.g. `%`, `tiles` |
| is_required | INTEGER NOT NULL DEFAULT 0 | |
| default_value | TEXT NULL | JSON-encoded scalar |
| display_hint | TEXT NULL | CHECK IN (`badge`,`stat_block`,`paragraph`,`hidden`) NULL allowed |
| sort_order | INTEGER NOT NULL DEFAULT 0 | |
| source_id | INTEGER NULL FK → content_sources.id ON DELETE CASCADE | |

Indexes: `UNIQUE(object_type_id, key)`.

### 1.5 `catalog_objects`
The central table.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | **stable internal identifier** referenced by everything else |
| source_id | INTEGER NOT NULL FK → content_sources.id ON DELETE CASCADE | owning pack |
| type_id | INTEGER NOT NULL FK → catalog_object_types.id ON DELETE RESTRICT | |
| template_key | TEXT NOT NULL | pack-local identifier (e.g. game `DataName`); **not globally unique** |
| slug | TEXT NOT NULL | human-friendly, source-scoped |
| name | TEXT NOT NULL | |
| short_description | TEXT NULL | |
| long_description | TEXT NULL | |
| tier | INTEGER NULL | generic progression tier, nullable |
| attributes | TEXT NOT NULL DEFAULT '{}' | JSON, validated against `catalog_attribute_definitions` at write time |
| primary_asset_id | INTEGER NULL FK → assets.id ON DELETE SET NULL | |
| overrides_object_id | INTEGER NULL FK → catalog_objects.id ON DELETE SET NULL | self-FK, patch semantics (`ADR-0013`) |
| replaces_object_id | INTEGER NULL FK → catalog_objects.id ON DELETE SET NULL | self-FK, full-replace semantics |
| patch | TEXT NULL | JSON, only meaningful when `overrides_object_id` is set |
| is_retired | INTEGER NOT NULL DEFAULT 0 | true when removed upstream but still referenced |
| is_orphaned_upstream | INTEGER NOT NULL DEFAULT 0 | true when removed upstream and referenced by campaign state (kept active, flagged) |
| is_user_authored | INTEGER NOT NULL DEFAULT 0 | |
| created_at | TEXT NOT NULL | |
| updated_at | TEXT NOT NULL | |

Indexes:
- `UNIQUE(source_id, template_key)`
- `UNIQUE(source_id, slug)`
- `INDEX(type_id)`, `INDEX(overrides_object_id)`, `INDEX(replaces_object_id)`
- `INDEX(name)` (simple prefix search); FTS5 virtual table `catalog_objects_fts(name, short_description, long_description, content='catalog_objects', content_rowid='id')` kept in sync via `AFTER INSERT/UPDATE/DELETE` triggers, for `FR-CAT-4` text search. **Not yet created** as of Phase 3 — `app/repositories/catalog_objects.py:search` currently uses a plain `LIKE` filter across `name`/`short_description`/`long_description`/`template_key`, acceptable at the catalog's current (near-zero) row count; adding the FTS5 table + triggers via a migration is a drop-in follow-up once real data volume justifies it, with no change to the repository's public `search()` signature.
- Expression index example for a hot JSON attribute: `CREATE INDEX idx_catalog_objects_attr_damage_min ON catalog_objects(CAST(json_extract(attributes, '$.damage_min') AS INTEGER));` added ad hoc per attribute that needs it (documented per migration, not all attributes).

Delete behaviour: `source_id ON DELETE CASCADE` — deleting a whole content source (an operation reserved for "remove a pack that was never actually imported/used", not for normal lifecycle, which uses retirement) removes its objects. `type_id ON DELETE RESTRICT` — a type in use cannot be deleted (retire it instead). Self-FKs `SET NULL` so removing a base object (should not normally happen — it would be retired instead) doesn't cascade-delete the overriding object.

### 1.6 `catalog_object_tags`
| Column | Type | Notes |
|---|---|---|
| object_id | INTEGER NOT NULL FK → catalog_objects.id ON DELETE CASCADE | |
| tag | TEXT NOT NULL | lowercase, normalized |

PK: `(object_id, tag)`. Index: `INDEX(tag)` for "browse by tag".

### 1.7 `catalog_relationship_types`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| slug | TEXT NOT NULL UNIQUE | e.g. `prerequisite` |
| name | TEXT NOT NULL | |
| inverse_type_id | INTEGER NULL FK → catalog_relationship_types.id ON DELETE SET NULL | self-FK |
| is_quantified | INTEGER NOT NULL DEFAULT 0 | whether `amount`/`unit` are meaningful |
| is_builtin | INTEGER NOT NULL DEFAULT 0 | |
| source_id | INTEGER NULL FK → content_sources.id ON DELETE CASCADE | |

### 1.8 `catalog_relationships`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| relationship_type_id | INTEGER NOT NULL FK → catalog_relationship_types.id ON DELETE RESTRICT | |
| source_object_id | INTEGER NOT NULL FK → catalog_objects.id ON DELETE RESTRICT | |
| target_object_id | INTEGER NOT NULL FK → catalog_objects.id ON DELETE RESTRICT | |
| group_key | TEXT NULL | NULL = singleton AND-group |
| group_logic | TEXT NULL | CHECK IN (`AND`,`OR`) NULL allowed, defaults conceptually to AND when group_key IS NULL |
| amount | NUMERIC NULL | quantity, e.g. resource cost |
| unit | TEXT NULL | |
| sort_order | INTEGER NOT NULL DEFAULT 0 | |
| attributes | TEXT NOT NULL DEFAULT '{}' | JSON, relationship-specific extras |
| source_pack_id | INTEGER NOT NULL FK → content_sources.id ON DELETE CASCADE | |
| is_user_authored | INTEGER NOT NULL DEFAULT 0 | |
| created_at | TEXT NOT NULL | |

Indexes: `UNIQUE(source_pack_id, relationship_type_id, source_object_id, target_object_id, group_key)` (prevents duplicate edges from repeated import); `INDEX(source_object_id)`; `INDEX(target_object_id, relationship_type_id)` (primary access path for "what does X require").
Delete behaviour: `RESTRICT` on both object FKs — an object with relationships must be retired, not hard-deleted, preserving graph integrity; `source_pack_id ON DELETE CASCADE` removes a pack's authored edges if the pack itself is removed.

### 1.9 `catalog_prerequisite_groups` / `catalog_prerequisite_group_members` (Phase 4, `ADR-0018`)

A compound (nested AND/OR) prerequisite expression tree, for requirements the flat `catalog_relationships.group_key` (single-level OR, ADR-0004) cannot represent, e.g. "(B AND C) OR (D AND E)". Most relationships still use the flat `group_key` mechanism; this pair of tables is only populated when a requirement genuinely nests.

#### `catalog_prerequisite_groups`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| target_object_id | INTEGER NOT NULL FK → catalog_objects.id ON DELETE RESTRICT | the object this (sub)tree gates |
| parent_group_id | INTEGER NULL FK → catalog_prerequisite_groups.id ON DELETE CASCADE | NULL = root group for `target_object_id` |
| logic | TEXT NOT NULL | CHECK IN (`AND`,`OR`) |
| relationship_type_id | INTEGER NOT NULL FK → catalog_relationship_types.id ON DELETE RESTRICT | which relationship kind this tree expresses (e.g. `prerequisite`) |
| source_pack_id | INTEGER NOT NULL FK → content_sources.id ON DELETE CASCADE | |
| created_at | TEXT NOT NULL | |

Indexes: `INDEX(target_object_id)`, `INDEX(parent_group_id)`.

#### `catalog_prerequisite_group_members`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| group_id | INTEGER NOT NULL FK → catalog_prerequisite_groups.id ON DELETE CASCADE | |
| catalog_object_id | INTEGER NULL FK → catalog_objects.id ON DELETE RESTRICT | a direct leaf requirement |
| child_group_id | INTEGER NULL FK → catalog_prerequisite_groups.id ON DELETE CASCADE | a nested sub-group (mutually exclusive with `catalog_object_id`) |
| amount | NUMERIC NULL | quantity, mirrors `catalog_relationships.amount` for quantified leaf requirements |
| unit | TEXT NULL | |
| sort_order | INTEGER NOT NULL DEFAULT 0 | |

CHECK: exactly one of `catalog_object_id`/`child_group_id` is set (XOR). Indexes: `INDEX(group_id)`, `INDEX(catalog_object_id)`, `INDEX(child_group_id)`.

### 1.10 `catalog_soldier_stat_profiles` (Phase 4, brief "Soldier stat profiles")

Ruleset- and (optionally) difficulty-aware default stats for a soldier/class, consumed by the future soldier-creation flow to initialize editable values (the user's actual stats always override these defaults, per the brief).

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| catalog_object_id | INTEGER NOT NULL FK → catalog_objects.id ON DELETE CASCADE | typically a `soldier_class` object |
| difficulty | TEXT NULL | CHECK IN (`rookie`,`veteran`,`commander`,`legend`) NULL allowed; NULL = applies at every difficulty |
| stat_key | TEXT NOT NULL | CHECK IN (`health`,`aim`,`defense`,`dodge`,`mobility`,`hacking`,`will`,`psi_offense`,`armor`,`combat_intelligence`) |
| stat_min | NUMERIC NULL | only set when the source documents a randomized range |
| stat_default | NUMERIC NULL | the single default/average value |
| stat_max | NUMERIC NULL | only set when the source documents a randomized range |
| is_randomized | INTEGER NOT NULL DEFAULT 0 | true when this ruleset randomizes the stat within [min, max] (e.g. "Not Created Equal") |
| source_pack_id | INTEGER NOT NULL FK → content_sources.id ON DELETE CASCADE | |
| created_at | TEXT NOT NULL | |

Indexes: `UNIQUE(catalog_object_id, difficulty, stat_key)`; `INDEX(catalog_object_id)`. Per `DATA_SOURCING.md`'s "never silently guess" rule, a row with unverified min/max is entered with `stat_min`/`stat_max` left NULL rather than invented — see `docs/DATA_SOURCING.md` §7 for which stats are currently verified vs. "not stated".

## 2. Asset tables

### 2.1 `assets`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| storage_path | TEXT NOT NULL UNIQUE | content-addressed relative path |
| content_hash | TEXT NOT NULL UNIQUE | sha256 hex |
| mime_type | TEXT NOT NULL | |
| width | INTEGER NULL | |
| height | INTEGER NULL | |
| origin | TEXT NOT NULL | CHECK IN (`upload`,`pack_bundled`,`fetched_url`,`generated_default`) |
| source_url | TEXT NULL | informational only |
| source_name | TEXT NULL | Phase 4, `ADR-0019`: human-readable name of the reference source (e.g. "UFOpaedia", "LWotC repository"), distinct from `source_url` |
| original_filename | TEXT NULL | the filename as provided by the import/upload, preserved for the asset manifest even though storage uses the content hash |
| license | TEXT NULL | |
| attribution_text | TEXT NULL | |
| redistribution_allowed | INTEGER NOT NULL DEFAULT 0 | gate for pack export, `ADR-0008` |
| import_status | TEXT NOT NULL DEFAULT 'imported' | CHECK IN (`imported`,`placeholder`,`pending`,`missing`) — asset-manifest status (brief "asset manifest ... import status") |
| created_at | TEXT NOT NULL | |

### 2.2 `object_assets`
| Column | Type | Notes |
|---|---|---|
| object_id | INTEGER NOT NULL FK → catalog_objects.id ON DELETE CASCADE | |
| asset_id | INTEGER NOT NULL FK → assets.id ON DELETE CASCADE | |
| role | TEXT NOT NULL | CHECK IN (`icon`,`portrait`,`banner`,`gallery`) |
| sort_order | INTEGER NOT NULL DEFAULT 0 | |

PK: `(object_id, asset_id, role)`. Index: `INDEX(asset_id)`.

## 3. Campaign tables (campaign-scoped; every table carries `campaign_id`, `ADR-0010`)

### 3.1 `campaigns`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT NOT NULL | |
| game_profile_slug | TEXT NULL | which preset it started from |
| difficulty | TEXT NOT NULL DEFAULT 'commander' | CHECK IN (`rookie`,`veteran`,`commander`,`legend`) |
| is_ironman | INTEGER NOT NULL DEFAULT 0 | |
| notes | TEXT NULL | |
| status | TEXT NOT NULL DEFAULT 'active' | CHECK IN (`active`,`archived`) |
| effective_catalog_version | INTEGER NOT NULL DEFAULT 0 | bumped to invalidate the effective-catalog cache (`ARCHITECTURE.md` §7) |
| created_at | TEXT NOT NULL | |
| updated_at | TEXT NOT NULL | |

### 3.2 `campaign_content_sources`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | |
| content_source_id | INTEGER NOT NULL FK → content_sources.id ON DELETE RESTRICT | a source in use by a campaign cannot be hard-deleted |
| enabled | INTEGER NOT NULL DEFAULT 1 | |
| precedence | INTEGER NOT NULL | lower = loads first, higher = overrides |
| created_at | TEXT NOT NULL | |

Indexes: `UNIQUE(campaign_id, content_source_id)`; `INDEX(campaign_id, precedence)`.

### 3.3 `campaign_object_state`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | surrogate key exists for FK convenience (plan items etc. may reference it), but **routes must always look it up by the natural key**, never by `id` alone (`ADR-0010`) |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | |
| catalog_object_id | INTEGER NOT NULL FK → catalog_objects.id ON DELETE RESTRICT | |
| status | TEXT NOT NULL DEFAULT 'unknown' | CHECK IN (`unknown`,`locked`,`available`,`in_progress`,`completed`,`owned`) |
| quantity | INTEGER NULL | meaningful for items/resources |
| prerequisite_override | INTEGER NOT NULL DEFAULT 0 | |
| notes | TEXT NULL | |
| state_attributes | TEXT NOT NULL DEFAULT '{}' | JSON |
| is_active | INTEGER NOT NULL DEFAULT 1 | false when owning object's source got disabled |
| completed_at | TEXT NULL | Phase 6 (`ADR-0021`): ISO timestamp the campaign completed the object; NULL unless `status` is `completed`/`owned` |
| completion_sequence | INTEGER NULL | Phase 6: per-campaign completion counter (1, 2, 3 …) so completion order survives equal timestamps |
| created_at | TEXT NOT NULL | |
| updated_at | TEXT NOT NULL | |

Indexes: `UNIQUE(campaign_id, catalog_object_id)` (the natural key); `INDEX(catalog_object_id)`.
Delete behaviour: `campaign_id CASCADE` (campaign delete wipes its state); `catalog_object_id RESTRICT` (an object with any campaign state must be retired, never hard-deleted, satisfying "deleting a campaign never touches the catalog" symmetrically — the catalog also can't be hard-deleted out from under a campaign).

### 3.4 `campaign_events`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | |
| catalog_object_id | INTEGER NULL FK → catalog_objects.id ON DELETE SET NULL | |
| event_type | TEXT NOT NULL | e.g. `research_completed`, `item_acquired`, `facility_built` (free text + app-level enum suggestions, not DB CHECK, since event vocabulary may grow with content types) |
| summary | TEXT NOT NULL | |
| in_game_label | TEXT NULL | e.g. "Month 3, Day 12" |
| occurred_at | TEXT NOT NULL | |

Indexes: `INDEX(campaign_id, occurred_at)`. Append-only: no UPDATE/DELETE routes in v1 beyond campaign deletion cascade.

### 3.5 `campaign_plans`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | |
| name | TEXT NOT NULL | |
| notes | TEXT NULL | |
| created_at | TEXT NOT NULL | |
| updated_at | TEXT NOT NULL | |

### 3.6 `campaign_plan_items`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| plan_id | INTEGER NOT NULL FK → campaign_plans.id ON DELETE CASCADE | |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | denormalized for mandatory-campaign_id repository queries without a join |
| catalog_object_id | INTEGER NOT NULL FK → catalog_objects.id ON DELETE RESTRICT | |
| action_hint | TEXT NULL | free text, e.g. "equip on Sgt. Reyes" |
| priority | INTEGER NOT NULL DEFAULT 0 | |
| sort_order | INTEGER NOT NULL DEFAULT 0 | |
| rationale | TEXT NULL | |
| depends_on_plan_item_id | INTEGER NULL FK → campaign_plan_items.id ON DELETE SET NULL | manual dependency, self-FK |
| cached_status | TEXT NOT NULL DEFAULT 'unknown' | CHECK IN (`blocked`,`ready`,`done`,`unknown`) — recomputed, not hand-edited |
| created_at | TEXT NOT NULL | |
| updated_at | TEXT NOT NULL | |

Indexes: `INDEX(campaign_id)`, `INDEX(plan_id, sort_order)`, `INDEX(catalog_object_id)`.

### 3.7 `campaign_soldiers`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | |
| name | TEXT NOT NULL | |
| class_object_id | INTEGER NULL FK → catalog_objects.id ON DELETE SET NULL | references a `soldier_class` object |
| rank | TEXT NULL | superseded by `rank_object_id` (Phase 7); kept, unused by new code, per the forward-only migration policy (`ADR-0007`) |
| surname | TEXT NULL | Phase 7 (`ADR-0022`) |
| callsign | TEXT NULL | Phase 7 |
| rank_object_id | INTEGER NULL FK → catalog_objects.id ON DELETE SET NULL | Phase 7: references a `soldier_rank` object, ruleset-aware like `class_object_id` |
| status | TEXT NOT NULL DEFAULT 'active' | CHECK IN (`active`,`wounded`,`dead`,`retired`) |
| portrait_asset_id | INTEGER NULL FK → assets.id ON DELETE SET NULL | |
| notes | TEXT NULL | |
| created_at | TEXT NOT NULL | |
| updated_at | TEXT NOT NULL | |

Indexes: `INDEX(campaign_id)`.

### 3.7.1 `campaign_soldier_stats` (Phase 7, `ADR-0022`)
One tracked stat of one soldier. `default_value` is the catalog class default captured **once at creation** and never recomputed; `actual_value` is the player's confirmed/edited real value. NULL means unknown/not tracked — never guessed.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | |
| soldier_id | INTEGER NOT NULL FK → campaign_soldiers.id ON DELETE CASCADE | |
| stat_key | TEXT NOT NULL | one of `catalog.SOLDIER_STAT_KEYS` (Phase 4) |
| actual_value | REAL NULL | the player's real soldier's value |
| default_value | REAL NULL | catalog default at creation time; frozen, never recalculated |
| is_confirmed | INTEGER NOT NULL DEFAULT 0 | false while showing an unconfirmed catalog default |

Constraints: `UNIQUE(soldier_id, stat_key)`. Indexes: `campaign_id`, `soldier_id`.

### 3.7.2 `campaign_builds` / `campaign_build_slots` / `campaign_soldier_builds` (Phase 7, `ADR-0022`)
The generic equipment-build model: `Build -> base catalog object -> typed slots -> attached catalog objects`, plus the assignment of a build to a soldier.

**`campaign_builds`**
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | |
| build_type | TEXT NOT NULL | open string (`"weapon"`, `"armor"`, ...), resolved via a code registry, never a CHECK enum |
| name | TEXT NOT NULL | |
| purpose | TEXT NULL | |
| notes | TEXT NULL | |
| base_object_id | INTEGER NOT NULL FK → catalog_objects.id ON DELETE RESTRICT | |

Constraints: `UNIQUE(campaign_id, build_type, name)`. Indexes: `campaign_id`, `base_object_id`.

**`campaign_build_slots`**
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | |
| build_id | INTEGER NOT NULL FK → campaign_builds.id ON DELETE CASCADE | |
| slot_type | TEXT NOT NULL | data, e.g. `"weapon_upgrade"`, `"utility"`, or any mod-introduced slot kind |
| slot_index | INTEGER NOT NULL DEFAULT 0 | |
| attached_object_id | INTEGER NOT NULL FK → catalog_objects.id ON DELETE RESTRICT | |
| is_override | INTEGER NOT NULL DEFAULT 0 | true = the player accepted a known compatibility issue manually |

Constraints: `UNIQUE(build_id, slot_type, slot_index)`. Indexes: `campaign_id`, `build_id`, `attached_object_id`.

**`campaign_soldier_builds`**
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | |
| soldier_id | INTEGER NOT NULL FK → campaign_soldiers.id ON DELETE CASCADE | |
| build_id | INTEGER NOT NULL FK → campaign_builds.id ON DELETE RESTRICT | points at the live build; never a snapshot (`ADR-0022` §4) |
| build_type | TEXT NOT NULL | denormalised from the build, lets a soldier have one assignment per type |
| assignment_slot | TEXT NOT NULL DEFAULT 'primary' | reserved for a future primary/secondary distinction |

Constraints: `UNIQUE(soldier_id, build_type, assignment_slot)`. Indexes: `campaign_id`, `soldier_id`, `build_id`.

### 3.8 `campaign_soldier_loadouts`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| soldier_id | INTEGER NOT NULL FK → campaign_soldiers.id ON DELETE CASCADE | |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | denormalized, see §3.6 rationale |
| slot | TEXT NOT NULL | e.g. `primary_weapon`, `utility_1`, `armor` |
| item_object_id | INTEGER NOT NULL FK → catalog_objects.id ON DELETE RESTRICT | |
| is_planned | INTEGER NOT NULL DEFAULT 0 | true = future build, false = current |
| created_at | TEXT NOT NULL | |

Indexes: `UNIQUE(soldier_id, slot, is_planned)` (one current + one planned item per slot); `INDEX(campaign_id)`.

### 3.9 `campaign_strategy_profiles`
| Column | Type | Notes |
|---|---|---|
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | |
| strategy_profile_id | INTEGER NOT NULL FK → strategy_profiles.id ON DELETE CASCADE | |
| enabled | INTEGER NOT NULL DEFAULT 1 | |

PK: `(campaign_id, strategy_profile_id)`.

### 3.10 `campaign_avenger_cells` (Phase 5, `ADR-0020`)
Exactly 12 rows per campaign: the campaign's own Avenger construction grid, 3 columns × 4 rows. Three independent concepts, three columns.

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | |
| coordinate | TEXT NOT NULL | stable position `R<row>C<col>` (`R1C1` … `R4C3`); CHECK `coordinate = 'R' \|\| grid_row \|\| 'C' \|\| grid_column` |
| grid_row | INTEGER NOT NULL | CHECK BETWEEN 1 AND 4 |
| grid_column | INTEGER NOT NULL | CHECK BETWEEN 1 AND 3 |
| room_state_object_id | INTEGER NULL FK → catalog_objects.id ON DELETE RESTRICT | **concept 1**: room / excavation state (catalog type `avenger_room_state`). NULL = the player has not configured this cell yet (the app never assumes a starting layout) |
| facility_object_id | INTEGER NULL FK → catalog_objects.id ON DELETE RESTRICT | **concept 2**: facility assignment (catalog type `facility`, stored as the effective-catalog winner id) |
| build_state | TEXT NOT NULL DEFAULT 'none' | **concept 3**: lifecycle of the assignment. CHECK IN (`none`,`planned`,`constructing`,`built`) |
| notes | TEXT NULL | |
| created_at, updated_at | TEXT NOT NULL | |

Constraints: `UNIQUE(campaign_id, coordinate)`, `UNIQUE(campaign_id, grid_row, grid_column)`; CHECK `(facility_object_id IS NULL AND build_state = 'none') OR (facility_object_id IS NOT NULL AND build_state <> 'none')`. Indexes: `campaign_id`, `room_state_object_id`, `facility_object_id`.

*Not stored*: "Available to build" / "Blocked" (derived from catalog prerequisites + room state, `ADR-0020`), duplicate eligibility (the facility's `allows_duplicates` attribute under the campaign's ruleset), power totals.

### 3.11 `campaign_research_queue` (Phase 6, `ADR-0021`)
A campaign's ordered research queue: projects the player intends to research (planned) or is researching (in progress; the status itself lives in `campaign_object_state`).

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | INTEGER NOT NULL FK → campaigns.id ON DELETE CASCADE | |
| catalog_object_id | INTEGER NOT NULL FK → catalog_objects.id ON DELETE RESTRICT | the effective-catalog winner id at the time it was queued; normalised on read if a ruleset override changes |
| position | INTEGER NOT NULL | CHECK `position >= 0`; the sole ordering key, renumbered `0..n-1` on every write |
| created_at | TEXT NOT NULL | |

Constraints: `UNIQUE(campaign_id, catalog_object_id)`. Indexes: `campaign_id`, `catalog_object_id`. Completing a project deletes its row. Catalog rows referenced here can only be retired, never hard-deleted.

## 4. Strategy (opinion) tables — separate from fact tables, `ADR-0009`

### 4.1 `strategy_profiles`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| slug | TEXT NOT NULL UNIQUE | |
| name | TEXT NOT NULL | |
| author | TEXT NULL | |
| description | TEXT NULL | |
| content_source_scope | INTEGER NULL FK → content_sources.id ON DELETE CASCADE | NULL = applies regardless of source |
| created_at | TEXT NOT NULL | |

### 4.2 `strategy_recommendations`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| strategy_profile_id | INTEGER NOT NULL FK → strategy_profiles.id ON DELETE CASCADE | |
| subject_object_id | INTEGER NULL FK → catalog_objects.id ON DELETE CASCADE | nullable to allow future query-based subjects |
| recommended_action | TEXT NOT NULL | free text / slug |
| rationale | TEXT NULL | |
| priority_hint | INTEGER NOT NULL DEFAULT 0 | |
| sort_order | INTEGER NOT NULL DEFAULT 0 | |

Indexes: `INDEX(strategy_profile_id)`, `INDEX(subject_object_id)`.

## 5. Migration bookkeeping

Migration history is tracked by Alembic's own `alembic_version` table (single-row, holds the current head revision id), managed entirely by `Flask-Migrate`/Alembic under `migrations/versions/*.py` (`ADR-0014`, supersedes the bespoke `schema_migrations` table originally specified here under `ADR-0007`). No application code reads or writes `alembic_version` directly.

## 6. Full ER overview (textual)

```
content_sources ─┬─< content_source_relations >─ content_sources
                  ├─< catalog_object_types >─< catalog_attribute_definitions
                  ├─< catalog_relationship_types (self: inverse_type_id)
                  └─< catalog_objects (self: overrides_object_id, replaces_object_id)
                         ├─< catalog_object_tags
                         ├─< object_assets >─ assets
                         ├─< catalog_relationships >─ catalog_objects (source/target)
                         ├─< catalog_prerequisite_groups (self: parent_group_id)
                         │      └─< catalog_prerequisite_group_members >─ catalog_objects / catalog_prerequisite_groups
                         └─< catalog_soldier_stat_profiles

campaigns ─┬─< campaign_content_sources >─ content_sources
           ├─< campaign_object_state >─ catalog_objects
           ├─< campaign_avenger_cells >─ catalog_objects (room state), catalog_objects (facility)
           ├─< campaign_research_queue >─ catalog_objects
           ├─< campaign_events >─ catalog_objects (nullable)
           ├─< campaign_plans ─< campaign_plan_items >─ catalog_objects (self: depends_on_plan_item_id)
           ├─< campaign_soldiers >─ catalog_objects (class, rank), assets (portrait)
           │      ├─< campaign_soldier_loadouts >─ catalog_objects (item)
           │      ├─< campaign_soldier_stats
           │      └─< campaign_soldier_builds >─ campaign_builds
           ├─< campaign_builds >─ catalog_objects (base)
           │      └─< campaign_build_slots >─ catalog_objects (attached)
           └─< campaign_strategy_profiles >─ strategy_profiles >─ strategy_recommendations >─ catalog_objects
```

## 7. Campaign isolation strategy (summary, `ADR-0010`)

- Every `campaign_*` table has `campaign_id ... ON DELETE CASCADE` directly or (for `campaign_plan_items`/`campaign_soldier_loadouts`) denormalized alongside the direct parent FK, so a single `DELETE FROM campaigns WHERE id=?` removes exactly one campaign's rows and nothing else.
- No `catalog_*` or `assets` table has a column referencing `campaigns`/`campaign_*` — verified by a schema-lint test (`tests/test_schema_lint.py`) that walks `sqlite_master`'s `foreign_key_list` and asserts no catalog table's FK target is a campaign table.
- `campaign_object_state`, `campaign_plan_items`, `campaign_soldier_loadouts` reference `catalog_objects` with `ON DELETE RESTRICT` — catalog rows in use are retired (`is_retired=1`), never deleted, so campaign FKs never dangle and never force a cascade back into the catalog.

## 8. Source precedence / override approach (summary, `ADR-0013`)

- `catalog_objects.overrides_object_id` (merge `patch`) / `replaces_object_id` (full replace) encode authored precedence links, validated at import against the authoring source's `content_source_relations` (`extends`).
- `campaign_content_sources.precedence` (per campaign) + `enabled` determine, at read time via `domain/effective_catalog.py`, which member of an override/replace chain is visible — never resolved by mutating rows at import or campaign-edit time.
- `(source_id, template_key)` uniqueness (not a global `template_key` uniqueness) is what allows unrelated sources to reuse template names safely.

## 9. Example seed data note

Resolved (Phase 2, see `IMPLEMENTATION_PLAN.md`): seeding is done by an idempotent `flask seed-dev` CLI command (`app/cli.py`), not baked into the Alembic migration itself — this keeps schema changes (structure) and reference data (content) as separate, independently re-runnable concerns, and avoids re-seeding on every `db upgrade`. It populates the four foundational `content_sources` rows (`xcom2-base`, `wotc`, `lwotc`, `user-local` — see `DOMAIN_MODEL.md` §3) and the builtin `catalog_object_types` (§`DOMAIN_MODEL.md` §4) and `catalog_relationship_types` (§`DOMAIN_MODEL.md` §6) rows with `is_builtin=1`, `source_id=NULL`. These are structural/reference seed data, not game content — no actual XCOM objects/relationships are seeded in Phase 2.

## 10. Migration changelog

| Revision | Summary |
|---|---|
| `7f5c4f4dfef7` | Initial schema (Phase 2): all tables through §1-5 as of that phase. |
| `56a40f219e11` | Phase 4: added `content_sources.source_repository`/`source_ref`/`source_commit_sha` (§1.1, provenance pinning, `ADR-0019`); added `catalog_prerequisite_groups`/`catalog_prerequisite_group_members` (§1.9, compound AND/OR prerequisites, `ADR-0018`); added `catalog_soldier_stat_profiles` (§1.10, soldier stat profiles); added `assets.source_name`/`original_filename`/`import_status` (§2.1, asset manifest fields). |

| `447c666ac239` | Phase 5: added `campaign_avenger_cells` (§3.10, per-campaign 3×4 Avenger grid with separate room state / facility assignment / build state, `ADR-0020`). Pure addition; no existing table changed. |
| `827415332929` | Phase 7: added `campaign_soldier_stats`, `campaign_builds`, `campaign_build_slots`, `campaign_soldier_builds` (§3.7.1/3.7.2) and `campaign_soldiers.surname`/`callsign`/`rank_object_id` (§3.7), `ADR-0022`. Pure additions; existing rows unaffected. |
| `41940602ac87` | Phase 6: added `campaign_research_queue` (§3.11) and `campaign_object_state.completed_at` / `completion_sequence` (§3.3) for research tracking, `ADR-0021`. Pure additions; existing rows unaffected. |

## 11. Curated game-data population (Phase 4, see `docs/DATA_INGESTION.md`)

This phase introduces the **curated content-pack importer** anticipated by `ARCHITECTURE.md` §5 and `ADR-0005`: a canonical, versioned, human-reviewable directory of game-fact JSON under `content/packs/<slug>/`, loaded into the tables above by explicit `flask content *` CLI commands (`app/services/content_import.py`, distinct from Phase 3's ad hoc interchange importer `app/services/catalog_import.py`/`ADR-0017`). No new tables were needed for the pack *format* itself — `catalog_objects`/`catalog_relationships`/`catalog_prerequisite_groups`/`catalog_soldier_stat_profiles` are the same tables the importer writes into that the UI and Phase 3's manual-authoring path already used. See `docs/DATA_SOURCING.md` for the authoritative-source hierarchy and `docs/DATA_INGESTION.md` for the ingestion/refresh/validation process.
