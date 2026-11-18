# ADR-0022: Soldier roster, stat provenance, and the generic equipment-build model

## Status
Accepted (Phase 7)

## Context
The player needs a per-campaign soldier roster (ruleset-aware class, catalog-derived default stats the player can confirm/edit without losing their edits) and reusable, named equipment configurations (initially Weapon and Armour builds) that work with base, WotC, LWotC and arbitrary mod content with no code change. The brief explicitly forbids hard-coding class names, stat lists, or a fixed set of component "slot" kinds (e.g. a `scope`/`repeater` column), and requires that unlocked-but-not-yet-built equipment remain plannable, not blocked.

## Decision

### 1. Stat provenance: one row per stat, `default_value` captured once, `actual_value` user-owned
`campaign_soldier_stats(campaign_id, soldier_id, stat_key, actual_value, default_value, is_confirmed)`, one row per tracked stat (`catalog.SOLDIER_STAT_KEYS`, unchanged from Phase 4). On creation, `app/services/soldiers.py:default_stats_for_class` reads the class's `catalog_soldier_stat_profiles` rows (Phase 4) at the campaign's difficulty and seeds `actual_value = default_value = catalog default`, `is_confirmed = False`. The user may then:
- **confirm** a default by re-submitting the same value (`is_confirmed = True`, state "confirmed"),
- **edit** it to their real unit's value (`is_confirmed = True`, state "custom" if different from the default, "confirmed" if the same),
- **clear** it (blank input → `actual_value = NULL`, state "blank").

**`default_value` is written once and never recomputed** — changing the soldier's class later does not touch any `campaign_soldier_stats` row, so a manually entered value can never be silently recalculated away. A stat the catalog does not state (e.g. Will for vanilla classes, not found in any verified source) is simply absent: no row, state "blank", never guessed. A ruleset with a randomized range (`is_randomized=True`, `stat_min`/`stat_max`) is shown as its documented **default** value, never a fake roll — the brief's "use the configured default/average profile rather than pretending the generated values are the soldier's true values."

### 2. Generic equipment-build model: `Build → base object → typed slots → attached objects`
- **`campaign_builds(campaign_id, build_type, name, purpose, notes, base_object_id)`** — `build_type` is an open string ("weapon", "armor", ...), resolved through a small in-code registry (`app/services/builds.py:BUILD_TYPES`) that maps a type to its base catalog type(s) and slot rules — **not** a CHECK constraint enumerating types, and not a column per component kind. Adding a third build type (e.g. "utility_loadout") needs a registry entry, no migration.
- **`campaign_build_slots(build_id, slot_type, slot_index, attached_object_id, is_override)`** — `slot_type` is itself data (`"weapon_upgrade"`, `"utility"`, or any string a mod's `build_slots` attribute introduces); `UNIQUE(build_id, slot_type, slot_index)`. No column is ever named after a specific component kind.
- **Slot counts come from the catalog, not code**: a weapon's `upgrade_slots` attribute and an armour's `utility_slots` attribute (both already catalog attributes since Phase 4) size the `weapon_upgrade`/`utility` slot rows. A base object (of any type) may additionally declare a `build_slots` JSON attribute (`[{"slot_type": "...", "count": N, "accepts": [...]}]`) for slot kinds the built-in registry does not special-case — a mod can therefore add a brand-new slot type with zero code changes. A base with no catalog count at all still gets a small number of slots (`unknown_slot_count`), each flagged `verified=False` so the editor is honest about the gap rather than refusing to let the player plan.
- **`campaign_soldier_builds(soldier_id, build_type, assignment_slot, build_id)`** — the assignment. `assignment_slot` (default `"primary"`) is reserved for a future primary/secondary distinction without a schema change; `UNIQUE(soldier_id, build_type, assignment_slot)` means re-assigning a slot replaces, never duplicates.

### 3. Compatibility: known-good, known-bad, or unverified — never silently blocked
`app/domain/builds.py:check_compatibility` classifies a component against a base using only catalog facts: an explicit `compatible_with` relationship (catalog `CatalogRelationship`, resolved per campaign ruleset like every other relationship), or a `compatible_weapon_categories` attribute matched against the base's `weapon_category`. **No data on either side means `"unverified"` — allowed, not blocked.** Only a component whose compatibility data *positively contradicts* the base is `"incompatible"`.

Other checked facts, all attribute-driven: `exclusive_group` (two components sharing a group conflict), `allows_duplicates_in_build` (`False` blocks a repeat, `True` allows it, absent/`None` allows it — the "controlled manual override" below exists for the `False` case, not for ignorance).

**Manual compatibility override**: `validate_build` accepts a caller-supplied `overrides: {slot_key}` set. An issue that is `overridable` (type mismatch, incompatible, exclusive conflict, duplicate) becomes a recorded `CampaignBuildSlot.is_override=True` row instead of a hard error when its slot is in that set; a non-overridable issue (no such slot, component outside the campaign's sources) is never bypassable. The editor UI surfaces this as an explicit "Allow anyway (manual compatibility override)" checkbox next to the specific incompatible choice — the user opts in per-component, the system never guesses "probably fine."

### 4. Builds are shared, not snapshotted
`campaign_soldier_builds.build_id` points at the live `campaign_builds` row. Editing a build is immediately visible to every soldier assigned to it (proven by `test_editing_a_shared_build_is_seen_by_every_assignment_immediately`). This was chosen over versioning/snapshotting because: (a) the brief's own example ("Close Assault Mk II") describes refining one named loadout over a campaign, which matches "the build is the thing," not "every edit is a new thing"; (b) snapshotting would silently fork data the user did not ask to fork, the kind of surprise `DECISION_MAKING`/this ADR explicitly avoids; (c) a user who *wants* two variants already has the obvious tool — Save As a differently-named build. Deleting a build in use is refused unless the caller explicitly confirms unassignment (`BuildInUseError` names every assigned soldier) — never a silent cascade that would quietly un-equip a soldier.

### 5. Availability integrates with Phase 6, never blocks planning
A build's `base_object_id` and every attached component are resolved through `app/services/dependency_index.py` (the same effective-catalog/override/status engine Research & the Avenger planner use). `BuildView.availability` is `"available"` only if every one of those objects is `satisfied` for the campaign; otherwise `"planned"` ("PLANNED / NOT YET AVAILABLE"), and `BuildView.missing` lists exactly which objects are not yet unlocked, each linking to `research.unlock_path` (Phase 6) — the brief's required integration point. Nothing about creating, editing or assigning a build is gated on availability.

### 6. Computed summary: known arithmetic only, free text never parsed
`app/domain/builds.py:summarize_build` adds component bonuses into a base attribute only for a small, explicit map of numeric semantics it actually understands (`aim_bonus`, `crit_bonus` → `crit_chance`, `clip_size_bonus` → `clip_size`). Everything else — an upgrade's free-text `effect_summary`, an unrecognised attribute — is displayed verbatim, never summed, with a note that it "is descriptive only." No build attempts to compute Combined stats it cannot justify arithmetically.

### 7. Ruleset-aware classes and ranks reuse the Phase 4/6 machinery unchanged
Classes and ranks are ordinary catalog objects (`soldier_class`, and a new `soldier_rank` type introduced via pack data, not a new table) read through the same effective-catalog/override resolution as everything else — an LWotC campaign sees LWotC's rank ladder and hero/base classes automatically; a mod adding a class needs only a catalog object with `type: soldier_class`. No class or rank name appears in application code.

## Consequences
- A soldier's roster and every build are per-campaign (`campaign_id` on every new table, `ON DELETE CASCADE`); catalog rows are only ever read.
- A thin weapon/armour-upgrade compatibility and tier dataset was added to the curated packs during this phase (Advanced/Superior upgrade tiers, `compatible_weapon_categories`/`exclusive_group`/`allows_duplicates_in_build` attribute *definitions* so packs can populate them) — but almost no real compatibility facts were verifiable from the sources read (`docs/DATA_SOURCING.md`), so most shipped upgrades are deliberately `"unverified"` rather than guessed. This is a known gap, not a design flaw: the mechanism is proven end-to-end by synthetic test packs that do supply the data.
- Future Weapon/Armour-adjacent build types (e.g. a PCS loadout, a grenade belt) are additive registry entries.

## Alternatives considered
- A fixed set of named columns per component kind (`scope_id`, `repeater_id`, ...): rejected outright by the brief ("Do not hard-code a database column named `scope`, `repeater`").
- Snapshotting/versioning builds on every edit: rejected (surprising, and the brief's own example implies refinement of one named thing — see §4).
- Blocking incompatible components outright with no override: rejected — a thin-metadata mod object must stay usable (brief: "provide a controlled allow manual compatibility override workflow rather than making the custom item unusable").
- Inventing numeric effects for every upgrade from its free-text description: rejected — "Do not invent arithmetic for free-text mod attributes."
- Storing `rank` as a free `TEXT` column (as the pre-Phase-7 schema did): superseded by `rank_object_id` → a `soldier_rank` catalog object, so ranks are ruleset-aware and consistent with every other "pick from the catalog" control; the old `rank` text column is kept (nullable, unused by new code) rather than dropped, since forward-only migrations never remove data a prior phase wrote (`ADR-0007`).
