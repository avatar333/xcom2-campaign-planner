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

`weapon`, `weapon_upgrade`, `armor`, `utility_item`, `ammo`, `grenade`, `resource`, `research_project`, `autopsy_project`, `proving_ground_project`, `facility`, `facility_upgrade`, `soldier_class`, `ability`, `technology`, `enemy`, `mission`, `strategy_upgrade`, `covert_action` *(LWotC)*, `faction`, `dark_event`.

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
- `campaign_soldiers` / `campaign_soldier_loadouts` — soldier: `name`, `class_object_id` (nullable FK into catalog `soldier_class` objects), `rank`, `status`, `portrait_asset_id`. Loadout: `slot`, `item_object_id`, `is_planned` (true = future build, false = current).
- `campaign_strategy_profiles` — which optional strategy profiles are enabled for this campaign (`ADR-0009`).

### 9.1 Availability (derived, never stored as fact)

For a catalog object in a campaign: walk its inbound `prerequisite`/`requires_*` relationships (respecting AND/OR groups), resolve each referenced object's `campaign_object_state.status` (or `prerequisite_override`); classify as `locked` (unmet requirement exists), `available` (all requirements met, not yet acted on), `in_progress`/`completed`/`owned` (from the object's own state), `unknown` (no state row and no determinable requirements — e.g. newly imported object). This computation is the same one driving plan-item `blocked`/`ready` flags (`FR-PLN-3`) and the "what am I missing" explanation (`FR-PLN-2`).

## 10. Invariants (must hold for all time, tested explicitly)

1. No `catalog_*` table ever has a foreign key to `campaigns` or any `campaign_*` table.
2. `campaign_object_state` rows only ever reference `catalog_objects.id`; deleting a campaign cascades only within `campaign_*` tables.
3. `(source_id, template_key)` is unique per object; `template_key` alone is never assumed unique.
4. A relationship's `source_object_id`/`target_object_id` always resolve to existing, non-deleted `catalog_objects` rows (FK, `ON DELETE RESTRICT` — retire, don't delete, referenced objects).
5. An object with `overrides_object_id`/`replaces_object_id` set must point at an object belonging to a source listed in its own source's `requires`/`extends` declarations (validated at import, `ADR-0013`).
6. Every `catalog_object_type` has exactly one `default_asset_id`; every rendered object resolves to a non-null image (primary asset or type default).
7. Strategy recommendations never appear in the same code path that computes Availability (`ADR-0009`).
