# Domain Model — XCOM 2 Campaign Planner

Status: Draft v1. Companion to `PRODUCT_SPEC.md`; the relational shape of everything below is normative in `DATABASE_SCHEMA.md`. This document defines the **ubiquitous language** — use these terms consistently in code, docs and UI copy.

## 1. Glossary

| Term | Definition |
|---|---|
| **Content Source** (a.k.a. **Pack**) | A named, versioned origin of game content: the base game, a DLC, War of the Chosen, a total conversion (LWotC), a Steam Workshop mod, or a user's own content. See §3. |
| **Catalog** | The union of all `catalog_objects`/`catalog_relationships` from every imported content source — the shared, campaign-agnostic "encyclopedia." |
| **Catalog Object** | A single game "thing" (a weapon, a research project, a facility, an enemy type, ...). Belongs to exactly one content source. Identified internally by a stable surrogate ID; see §5. |
| **Object Type** | The category of a catalog object (weapon, research, facility, ...). Itself a data row (`catalog_object_types`), not a hard-coded enum — see §4. |
| **Attribute** | A type-specific, validated key/value fact about an object (e.g. `tier`, `clip_size`, `science_score`). Declared per object type in `catalog_attribute_definitions`; stored in the object's JSON `attributes` field. |
| **Template Name / Template Key** | The game/mod's own identifier for an object (e.g. an XCOM 2 `DataName` such as `WeaponLaserRifle` or `MagnetecCoils`). Unique only *within* its content source, never assumed globally unique — see §5. |
| **Relationship** | A typed, directed, provenance-carrying edge between two catalog objects (e.g. *Hybrid Materials* `prerequisite`→ *Magnetic Weapons*). See §6. |
| **Relationship Type** | The kind of a relationship (prerequisite, unlocks, requires-resource, ...), itself data, extensible — see §6. |
| **Override / Patch** | A mechanism by which one source's object supersedes or amends another source's object without mutating it, used to express total-conversion/mod precedence — see §7 and `ADR-0013`. |
| **Effective Catalog** | The *resolved view* of the catalog for one specific campaign, after applying that campaign's enabled sources, their precedence order, and override/patch resolution. Never stored; computed/cached per campaign. |
| **Asset** | A locally-stored image (or future media) file with provenance/licensing metadata, attachable to objects or campaign entities with a role (icon, portrait, banner, gallery) — see §8. |
| **Campaign** | One independent playthrough: a named save-the-planning-not-the-save-file container with its own enabled sources, object state, events, plans and soldiers. See §9. |
| **Campaign Object State** | The campaign-scoped mutable facts about one catalog object for one campaign (status, quantity, notes, overrides). Never shared between campaigns. |
| **Availability** | A *derived*, not stored, classification of a catalog object for a campaign: `locked` / `available` / `in_progress` / `completed` / `unknown`, computed from relationships + state. |
| **Plan / Plan Item** | A campaign-scoped list of intended future actions ("research X", "build Y") with priority/order/rationale, whose readiness is derived from Availability + manual dependencies. |
| **Strategy Profile / Recommendation** | An optional, attributable, togglable layer of community/opinionated advice, modeled and stored separately from game facts — see `ADR-0009`. |
| **Soldier** | A campaign-scoped roster member with class, rank, status and current/planned loadouts. Not a tactical-combat simulation entity. |
| **Game Profile** | A named preset bundle of content sources + defaults used to prefill a new campaign (e.g. "WotC Vanilla", "LWotC"). A convenience, not a new kind of state. |

## 2. Context map (bounded contexts)

```
┌────────────────────┐      references by id       ┌───────────────────────┐
│  Catalog Context    │ ───────────────────────────▶│  Campaign Context      │
│  (content-agnostic) │                              │  (per-playthrough)     │
│  sources, objects,  │◀── never written to by ──────│  state, plans, events, │
│  relationship types │      campaign context         │  soldiers, strategy    │
└─────────┬───────────┘                              └───────────┬────────────┘
          │ owns                                                 │ owns
          ▼                                                      ▼
   ┌──────────────┐                                      ┌───────────────────┐
   │ Asset Context │◀──referenced by both, owned by neither──▶│ (shared, immutable per file) │
   └──────────────┘                                      └───────────────────┘
```

The Catalog context is **never** aware a campaign exists. The Campaign context only ever holds foreign keys into the Catalog context, never copies of its data (`ADR-0002`). The Asset context is a leaf dependency of both.

## 3. Content Source

A content source models *where game data came from* and *how it relates to other sources*.

Fields (normative in `DATABASE_SCHEMA.md` §`content_sources`):
- `slug` (stable machine identifier, e.g. `xcom2-base`, `wotc`, `lwotc`, `workshop-1234567890`, `user-local`), `name`, `kind` ∈ {`base`, `dlc`, `total_conversion`, `workshop_mod`, `user`}, `version`, `steam_workshop_id` (nullable), `description`, `license`, `attribution_text`, `homepage_url`, `is_enabled_by_default`.
- Declared relations to other sources: `requires` (hard dependency, e.g. an Alien Hunters pack requiring base), `conflicts` (mutually exclusive), `extends`/`overrides` (this source may patch objects from the named source — required before any `catalog_objects.overrides_object_id` cross-source link is accepted at import time).
- Provenance/import bookkeeping: `import_checksum`, `imported_at`, `is_builtin` (true for curated base/DLC/LWotC packs the app ships *data* for — not binary assets, see `ADR-0008`).

A **user source** is simply a source with `kind='user'`, created implicitly the first time a user authors a custom object/mod entry, or explicitly named by the user (e.g. "My Workshop: Better Pistols"). It is where `FR-SRC-6`/manually-created mod objects live.

## 4. Object Types (categories)

Object types are rows, not code (`ADR-0003`). The seed list to ship with the base/WotC/LWotC packs (non-exhaustive, extensible by any pack):

`weapon`, `weapon_upgrade`, `armor`, `utility_item`, `ammo`, `grenade`, `resource`, `research_project`, `autopsy_project`, `proving_ground_project`, `facility`, `facility_upgrade`, `soldier_class`, `soldier_rank` *(Phase 7)*, `ability`, `technology`, `enemy`, `mission`, `strategy_upgrade`, `covert_action` *(LWotC)*, `faction`, `dark_event`.

Each `catalog_object_type` row: `slug`, `name`, `description`, `icon_asset_id` (type-level fallback glyph), `sort_order`, `is_builtin`. Each type owns zero or more `catalog_attribute_definitions` (key, data_type ∈ {`string`,`integer`,`number`,`boolean`,`enum`,`text`}, `enum_values` JSON when applicable, `unit`, `is_required`, `default_value`, `display_hint` ∈ {`badge`,`stat_block`,`paragraph`,`hidden`,...}, `sort_order`).

New categories (e.g. LWotC's "Covert Action") are added by a pack shipping a new `catalog_object_types` row — no migration (`FR-CAT-2`).

## 5. Catalog Object identity

Every catalog object has:
- **Internal ID**: an opaque integer surrogate primary key (`catalog_objects.id`), stable for the row's lifetime, the *only* ID ever used by relationships, campaign state, or assets. This is the "stable internal identifier" required by the brief.
- **Template key**: `catalog_objects.template_key`, the pack author's own identifier (ideally the game's own `DataName`/template name, e.g. `WeaponLaserRifle`), preserved for traceability and for the importer to upsert against. **Unique only within `(source_id, template_key)`**, never globally — two unrelated sources may legitimately reuse the same template key without collision (`DOMAIN_MODEL` §1, `ADR-0013`).
- **Slug**: a human-friendly, source-scoped URL slug derived from name, for readable routes (`/catalog/objects/wotc/magnetic-rifle`), cosmetic only, not an identity key.

Common normalized fields for every object regardless of type: `type_id`, `source_id`, `name`, `short_description`, `long_description`, `tier` (nullable generic progression-tier integer, not weapon-specific — see `ADR-0012`), `tags` (via `catalog_object_tags`), `is_retired`, `is_user_authored`, `overrides_object_id`/`replaces_object_id` (nullable self-FK, see §7), `patch` (nullable JSON, only set when `overrides_object_id` is set), `primary_asset_id`, `created_at`/`updated_at`.

Type-specific facts live in the validated `attributes` JSON (`ADR-0012`), e.g.:
- `weapon`: `damage_min`, `damage_max`, `crit_chance`, `clip_size`, `aim_bonus`, `range_class`.
- `research_project`: `science_score`, `credit_cost`, `breakthrough` (bool).
- `facility`: `power_cost`, `grid_slot_requirement`, `staff_slots`.
- `soldier_class`: `role_tags`, `abilities_by_rank` (reference list, cross-checked against `ability` objects via relationships, not duplicated here).
- `soldier_rank` (Phase 7, `ADR-0022`): a ruleset's promotion ladder; `tier` orders ranks within a ruleset. A total-conversion ruleset authors its own rank objects rather than overriding vanilla's (no verified 1:1 mapping exists between e.g. LWotC's 9-rank ladder and vanilla's 8).
- `weapon`/`weapon_upgrade`/`armor` gain build-related attributes in Phase 7: `upgrade_slots`/`utility_slots` size a build's typed component slots; `build_slots` (JSON) lets any base object declare additional mod-defined slot kinds; `compatible_weapon_categories`, `compatible_with` (relationship), `exclusive_group`, `allows_duplicates_in_build` express known build-compatibility facts (`ADR-0022`) — absent data means "unverified", never an assumed match.

## 6. Relationships

A relationship connects a `source_object_id` to a `target_object_id` with a `relationship_type_id`, directional and explicit (see `ADR-0004`). Seed relationship types and their meaning:

| Type | Meaning | Inverse |
|---|---|---|
| `prerequisite` | source must be satisfied before target becomes available | `unlocks` |
| `unlocks` | source, once satisfied, makes target available (stored as the inverse of an authored `prerequisite`; may also be authored directly for one-to-many "this unlocks these 5 things" packs) | `prerequisite` |
| `upgrades_to` | source item can be upgraded into target | `upgraded_from` |
| `requires_facility` | target requires source facility to exist | `enables` |
| `produced_by` | target is produced/built at source (facility/project) | `produces` |
| `requires_resource` | target consumes `amount` units of source (a `resource` object) | `consumed_by` |
| `class_can_equip` | source soldier_class may equip target item | `equippable_by_class` |
| `compatible_with` | symmetric compatibility hint (e.g. weapon_upgrade compatible with weapon) | `compatible_with` |
| `replaces` | target is a straight replacement of source (no merge) | `replaced_by` |
| `supersedes` | target's rules take precedence over source's for the same concept (used for cross-source override wiring, `ADR-0013`) | `superseded_by` |
| `requires_research` | target requires source (a `research_project`) completed — a semantic specialization of `prerequisite` kept distinct for UI grouping/readability | `required_by_research` |

Packs may add new relationship types (e.g. LWotC `requires_covert_action`) without code changes.

**Grouping semantics** (`ADR-0004`): rows for the same `(target_object_id, relationship_type_id)` are AND-ed by default. Rows that additionally share a non-null `group_key` with `group_logic='OR'` form an alternative set — satisfying any one member satisfies the group. `amount`/`unit` quantify `requires_resource` (and similarly-quantifiable types); null for boolean relationships.

Every relationship carries `source_pack_id` (which content source authored this edge) and `is_user_authored`, mirroring object provenance.

## 7. Override / precedence model (summary)

See `ADR-0013` for the full algorithm. Domain-level summary: an object may declare `overrides_object_id` (merge `patch` over the base) or `replaces_object_id` (full replacement, no inheritance) pointing at an object in a source it `requires`/`extends`. A campaign's enabled source list + precedence order determines, per identity group, which object "wins" in the **effective catalog** — the base rows are never mutated, so disabling the overriding source instantly reverts behaviour to the base object.

## 8. Assets

An `asset` is a locally-stored file with `storage_path` (content-addressed), `mime_type`, `width`/`height`, `origin` ∈ {`upload`,`pack_bundled`,`fetched_url`,`generated_default`}, `source_url` (informational, never fetched automatically), `license`, `attribution_text`, `redistribution_allowed`. Objects attach assets via `object_assets (object_id, asset_id, role, sort_order)`, `role` ∈ {`icon`,`portrait`,`banner`,`gallery`}. Every `catalog_object_type` has a `default_asset_id` fallback so every object resolves to *some* image (`ADR-0006`). Campaign entities (soldiers) reuse the same `assets`/`*_assets` pattern for portraits.

## 9. Campaign

A campaign is the isolation boundary (`ADR-0002`, `ADR-0010`). Fields: `name`, `game_profile_slug` (nullable, which preset it started from), `difficulty` ∈ {`rookie`,`veteran`,`commander`,`legend`}, `is_ironman`, `notes`, `status` ∈ {`active`,`archived`}, `created_at`/`updated_at`.

A campaign owns, exclusively:
- `campaign_content_sources` — which sources are enabled and in what precedence order (§7).
- `campaign_object_state` — per-object mutable state, keyed `(campaign_id, catalog_object_id)`: `status` ∈ {`unknown`,`locked`,`available`,`in_progress`,`completed`,`owned`} (semantics may differ slightly by object type but the enum is generic — e.g. `completed` for research, `owned` for items with `quantity`), `quantity` (nullable integer, meaningful for items/resources), `prerequisite_override` (user asserts a prerequisite is satisfied by an external effect), `notes`, `state_attributes` JSON (campaign-specific type-varying state, same validation pattern as catalog attributes but against a *state* schema), `is_active` (false when the owning object's source got disabled — `FR-CMP-3`), timestamps.
- `campaign_events` — append-only log: `event_type`, `catalog_object_id` (nullable), `summary`, `in_game_label` (free text, no simulated calendar — `PRODUCT_SPEC.md` A7), `occurred_at`.
- `campaign_plans` / `campaign_plan_items` — `plan_items`: `catalog_object_id`, `action_hint` (free text, e.g. "equip on Sgt. Reyes"), `priority`, `sort_order`, `rationale`, `depends_on_plan_item_id` (nullable self-FK, manual dependency), `status` (derived `blocked`/`ready` cached + recomputed on demand).
- `campaign_soldiers` / `campaign_soldier_loadouts` — soldier: `name`, `surname`, `callsign`, `class_object_id` (nullable FK into catalog `soldier_class` objects), `rank_object_id` (Phase 7, FK into catalog `soldier_rank` objects; the legacy free-text `rank` column is superseded but kept), `status`, `portrait_asset_id`. Loadout: `slot`, `item_object_id`, `is_planned` (true = future build, false = current) -- superseded by the Phase 7 build-assignment model below for weapon/armour equipment, kept for other slot kinds not yet migrated.
- `campaign_soldier_stats` (Phase 7, `ADR-0022`) — one row per tracked stat (`health`, `aim`, ...): `actual_value` (the player's real soldier), `default_value` (the catalog class default, captured once at creation and never recomputed), `is_confirmed`. A stat with no catalog default and no user entry simply has no row.
- `campaign_builds` / `campaign_build_slots` / `campaign_soldier_builds` (Phase 7, `ADR-0022`) — the generic equipment-build model: a named, campaign-owned build (`build_type` open-ended, e.g. `weapon`/`armor`) over a catalog base object, with typed slots (`slot_type` + `slot_index`) holding attached catalog objects (`is_override` when the player accepted a known compatibility issue), assigned to a soldier by `(build_type, assignment_slot)`. Assignments reference the live build, never a snapshot.
- `campaign_avenger_cells` — the campaign's own 12-cell Avenger construction grid (3 columns × 4 rows, stable coordinates `R1C1`…`R4C3`, `ADR-0020`). Each cell holds three **independent** facts: a *room state* (catalog `avenger_room_state`, e.g. cleared / Alien Debris / Alien Machinery / Exposed Power Coil; unset until the player records it), a *facility assignment* (catalog `facility`), and a *build state* (`none` → `planned` → `constructing` → `built`). "Available to build" and "Blocked" are derived for planned facilities, never stored.
- `campaign_strategy_profiles` — which optional strategy profiles are enabled for this campaign (`ADR-0009`).

### 9.1 Availability (derived, never stored as fact)

For a catalog object in a campaign: walk its inbound `prerequisite`/`requires_*` relationships (respecting AND/OR groups), resolve each referenced object's `campaign_object_state.status` (or `prerequisite_override`); classify as `locked` (unmet requirement exists), `available` (all requirements met, not yet acted on), `in_progress`/`completed`/`owned` (from the object's own state), `unknown` (no state row and no determinable requirements — e.g. newly imported object). This computation is the same one driving plan-item `blocked`/`ready` flags (`FR-PLN-3`) and the "what am I missing" explanation (`FR-PLN-2`).

### 9.2 Avenger grid rules (`ADR-0020`)

- A campaign has exactly 12 cells with unique coordinates; position is data, never display order.
- A facility is offered in a campaign only if it is an effective-catalog `facility` whose `buildable_in_avenger_grid` is true under that campaign's sources/ruleset. Duplicates follow the facility's `allows_duplicates` attribute as resolved for the ruleset (default: unique).
- Planning is never prevented by unmet prerequisites or an uncleared room; those are reported as blockers (`Blocker`). Invalid *identity* (not a buildable facility for the campaign, duplicate of a unique facility, invalid coordinate/state) is rejected.
- A facility under construction or built is never reported as blocked (it exists in the player's game).
- Prerequisites are read from catalog relationships (`prerequisite`, `unlocks`, `requires_research`, `requires_facility`, grouped by `group_key`) and compound groups (`ADR-0018`), including edges on objects the facility overrides; satisfied by `campaign_object_state.status ∈ {completed, owned}` / `prerequisite_override`, or a built cell for facility requirements.

### 9.3 Research and dependency semantics (`ADR-0021`)

- **Campaign research states**: *completed* (recorded in `campaign_object_state`, with `completed_at` and a per-campaign `completion_sequence`), *in progress*, *planned* (present in the ordered research queue), *available* (not yet acted on, all requirements met) and *locked* (an unmet requirement exists). Only completed / in progress / planned are recorded; *available* and *locked* are derived and never stored. A facility reads built / constructing / available to build.
- **Requirement expression**: an object's requirement roots are AND-ed; a group is AND or OR per its `group_logic`; groups nest (`ADR-0018`). OR is never inferred from the absence of AND.
- **Steps vs unlocks**: research, autopsy, Proving Ground, technology, facility (and similar) objects are *steps* the player completes; weapons, armour, items, ammo and grenades are *derived* — satisfied automatically when their requirements are. Resources are shown with amounts but never block.
- **Unlock path**: all ancestors reachable through any alternative, with a gate node per OR; steps needed on *every* route are distinguished from steps that belong to only some alternatives. No alternative is ranked above another.
- **Queue validation**: an item is blocked at position *n* if a requirement is unmet given everything already satisfied plus the queue items before *n*. Blocked items are warnings, never errors.
- **Facts vs strategy**: requirements/unlocks/blockers are facts from the catalog graph; any "research this early" advice belongs to strategy profiles (`ADR-0009`) and enters only through the `RecommendationProvider` seam.

### 9.4 Equipment builds (`ADR-0022`)

- A build's slots, their count and what they accept come entirely from catalog attributes (`upgrade_slots`/`utility_slots`/`build_slots`) and a small code registry of build *types* (not component kinds); no slot kind is ever a dedicated database column.
- Compatibility between a component and a base is **known-compatible**, **known-incompatible**, or **unverified** (no data either way) -- only known-incompatible blocks, and only until the player explicitly accepts a manual override, which is recorded (`is_override=1`), never silent.
- A build's availability ("available" vs. "PLANNED / NOT YET AVAILABLE") is derived from whether its base and every attached component are unlock-satisfied for the campaign (Phase 6 dependency index); it is never stored and never prevents saving, editing or assigning the build.
- A numeric build summary is computed only for attribute pairs whose arithmetic is explicitly known; a component's free-text effect is shown, never parsed or summed.

## 10. Invariants (must hold for all time, tested explicitly)

1. No `catalog_*` table ever has a foreign key to `campaigns` or any `campaign_*` table.
2. `campaign_object_state` rows only ever reference `catalog_objects.id`; deleting a campaign cascades only within `campaign_*` tables.
3. `(source_id, template_key)` is unique per object; `template_key` alone is never assumed unique.
4. A relationship's `source_object_id`/`target_object_id` always resolve to existing, non-deleted `catalog_objects` rows (FK, `ON DELETE RESTRICT` — retire, don't delete, referenced objects).
5. An object with `overrides_object_id`/`replaces_object_id` set must point at an object belonging to a source listed in its own source's `requires`/`extends` declarations (validated at import, `ADR-0013`).
6. Every `catalog_object_type` has exactly one `default_asset_id`; every rendered object resolves to a non-null image (primary asset or type default).
7. Strategy recommendations never appear in the same code path that computes Availability (`ADR-0009`).
8. Every campaign has exactly 12 `campaign_avenger_cells` with unique `(campaign_id, coordinate)`; a cell's facility is NULL if and only if its build state is `none`; no catalog table references the cell table (`ADR-0020`).
9. A research queue holds each catalog object at most once per campaign; completing a project removes it; a campaign's progression (state, queue, completion order) never alters or is derived from another campaign's, and never mutates a catalog row (`ADR-0021`).
10. A soldier's stats and build assignments belong to exactly one campaign; a `campaign_soldier_stats.default_value` is written once at creation and is never overwritten by a later class change; a build assigned to a soldier is never silently deleted or unassigned (`ADR-0022`).
