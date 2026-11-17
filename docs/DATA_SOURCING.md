# Data Sourcing — XCOM 2 Campaign Planner

Status: Draft v1. Explains where **factual** catalog/pack data should come from, how it is verified, and how it differs from **strategy** data (`ADR-0009`). This document guides whoever authors `content/packs/*` JSON, not the application code.

## 1. Principle

Facts (names, template IDs, numeric costs, prerequisite/unlock relationships) are sourced from authoritative game/mod material. Community opinion (what to prioritize, "meta" builds) is sourced from community discussion but is **never** written into `catalog_objects`/`catalog_relationships` — it goes into `strategy_profiles`/`strategy_recommendations` (`ADR-0009`, `DOMAIN_MODEL.md` §1).

## 2. Authoritative sources, by content source

| Content source | Primary factual reference | Secondary/cross-check |
|---|---|---|
| XCOM 2 base game | UFOpaedia XCOM 2 pages (ufopaedia.org/index.php/XCOM_2), official SDK template defaults (`DefaultGameData*.ini`, `X2TechTemplate`/`X2ItemTemplate`/`X2FacilityTemplate` class defaults) | Steam Community modding guides for template/DataName conventions |
| War of the Chosen | UFOpaedia WotC-tagged pages, SDK WotC template deltas | Community wikis (fextralife) for cross-reference only |
| Alien Hunters / Shen's Last Gift / Tactical Legacy Pack | UFOpaedia DLC pages | — |
| LWotC | LWotC GitHub source (`github.com/long-war-2/lwotc`) `Config/*.ini` and `.uc` template-modification source (ground truth for *exact* numbers/requirements), LWotC documentation site | UFOpaedia "(LWOTC)"-suffixed pages for human-readable summaries |
| Steam Workshop mods | The mod's own Workshop page description + its `Config/XCom*.ini` if the user has it locally | Author's own documentation/readme |
| User-created content | The user themselves | — |

## 3. Template/class reference (for authoring `template_key` and `attributes`)

XCOM 2's modding SDK exposes game content as UnrealScript template objects, retrieved by a stable `DataName`, which is exactly the concept this app calls `template_key` (`DOMAIN_MODEL.md` §5):

- `X2ItemTemplate` (+ subclasses for weapons/armor/utility/grenades/ammo) — managed by `X2ItemTemplateManager`.
- `X2TechTemplate` (research/autopsies/proving-ground) — requirements expressed via `Requirements.RequiredTechs` (→ our `prerequisite` relationship), `RequiredItems`/`RequiredEquipment` (→ `prerequisite` on the item object, or a dedicated relationship type if semantically distinct), `RequiredScienceScore` (→ an `attributes.science_score` fact, not a relationship), `Cost.ResourceCosts`/`Cost.ArtifactCosts` (→ `requires_resource` relationships with `amount`).
- `X2FacilityTemplate` / `X2FacilityUpgradeTemplate` — managed via `X2StrategyElementTemplateManager`; requirements follow the same `Requirements` struct pattern.
- `X2SoldierClassTemplate` — managed by `X2SoldierClassTemplateManager`; class→ability and class→weapon-category associations map to `class_can_equip`/`ability` relationships.
- `X2AbilityTemplate` — managed by `X2AbilityTemplateManager`.

Config files of interest: `DefaultGameData.ini`, `DefaultGameData_WeaponData.ini`, `DefaultGameData_CharacterStats.ini`, `DefaultClassData.ini` (base/WotC); LWotC ships its own `Config/XComLW_Overhaul.ini` and `.uc` template-modification classes that patch base templates by `DataName` at `OnPostTemplatesCreated()` — this is the real-game precedent for this app's `overrides_object_id`/`patch` mechanism (`ADR-0013`): LWotC does not duplicate the base template, it finds it by `DataName` and mutates specific fields, which is exactly "patch over base" semantics.

Extracting pack JSON directly from a user's local `.ini`/compiled templates (automated) is out of scope for v1 (`FR-SRC-9`, future) — packs are hand-authored JSON informed by the references above.

## 4. Known factual relationships to seed in the base/WotC pack (illustrative, non-exhaustive)

Verified against UFOpaedia research pages at authoring time; the importer's dry-run diagnostics (`FR-SRC-7`) should be used to validate the final pack, not this table:

- `Alien Biotech` — no prerequisites; unlocks autopsy projects and the Proving Ground facility.
- `Hybrid Materials` — foundational; prerequisite to `Magnetic Weapons` (together with ADVENT Officer autopsy) and to combat-armor research.
- `Magnetic Weapons` — prerequisites: `Hybrid Materials` AND `ADVENT Officer Autopsy`; unlocks magnetic-tier weapons.
- `Advanced Magnetic Weaponry` — prerequisite: `Magnetic Weapons`.
- `Plasma Rifle` — prerequisite: an **OR group** of (`Advanced Laser Weapons`) or (`Advanced Coilguns`), modeled as two `prerequisite` rows sharing `group_key='plasma-rifle-path'`, `group_logic='OR'` (`ARCHITECTURE.md` §5.3 example); unlocks Beam Cannon/Plasma Lance/Storm Gun.
- `Psionics` — prerequisite: `Sectoid Autopsy`; unlocks the Psi Lab facility and Psi Operative soldier class training.
- `Shadow Chamber` (facility unlock) — prerequisite: story tech (`Alien Encryption`/`Codex Brain` branch).
- `Proving Ground` (facility) — prerequisite: `ADVENT Trooper Autopsy`.
- Resources (`Supplies`, `Intel`, `Alien Alloys`, `Elerium Crystals`, `Elerium Cores`, `Corpses: <species>`) are modeled as `resource`-type catalog objects; research/build costs reference them via `requires_resource` relationships with `amount`.

LWotC reauthors several of these numbers/requirements (e.g. different `RequiredScienceScore`, added Covert Action gates) — modeled as `overrides`/`patch` objects in the `lwotc` pack pointing at the corresponding `wotc`/`xcom2-base` objects, per `ADR-0013` and `ARCHITECTURE.md` §5.2.

## 5. Strategy/opinion sourcing (kept out of catalog facts)

Community strategy guides (Steam Community guides, Reddit `r/Xcom`/`r/LongWarOfTheChosen` discussions, YouTube build guides) may inform `strategy_profiles`/`strategy_recommendations` entries — e.g. "rush Guerrilla Tactics School and Resistance Comms in the first two months" — each recommendation keeps an `author`/`rationale` field crediting its source informally. These are explicitly advisory, togglable per campaign, and visually distinguished (`UI_UX_SPEC.md` §3.9) from the deterministic requirement facts above.

## 6. Licensing note

This document and the pack JSON it informs contain only **facts** (names, numbers, relationships) paraphrased in original wording — not copied verbatim prose from any wiki, and no bundled images (`ADR-0008`). Attribution fields (`content_sources.attribution_text`, `homepage_url`) credit LWotC and other mod authors where their design decisions are reflected in pack data.
