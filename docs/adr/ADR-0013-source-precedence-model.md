# ADR-0013: Source precedence and override/patch resolution algorithm

## Status
Accepted

## Context
A total-conversion mod (LWotC) must be able to change or supersede base-game data (e.g. redefine `Magnetic Weapons`' requirements, retune a facility) without deleting/duplicating the base object, and without every other feature needing bespoke "which value wins" logic. A campaign enables an ordered set of sources; the *effective* catalog for that campaign is a function of that order.

## Decision
1. **Identity vs. override are different relations.** A pack may `override` a specific object from a *different* source it declares in `requires`/`extends`: this creates a new `catalog_objects` row (its own source, template_key) but with `overrides_object_id` pointing at the base row, plus a `patch` JSON (subset of fields/attributes actually changed — not a full copy) OR, for full replacements, `replaces_object_id` with no base-field inheritance. `catalog_relationship_types` includes `overrides`/`supersedes` wiring these at the relationship level too (e.g. LWotC's Magnetic Weapons research superseding vanilla's prerequisites).
2. **Campaign source ordering defines precedence.** `campaign_content_sources (campaign_id, content_source_id, enabled, precedence INTEGER, UNIQUE(campaign_id, content_source_id))`; lower `precedence` number loads first, higher overrides. When a campaign is created from a *game profile* (e.g. "LWotC"), the profile's default source list + precedence is pre-filled (`FR-CMP-2`).
3. **Effective-catalog resolution** (`app/domain/effective_catalog.py`, cached per campaign and invalidated when `campaign_content_sources` or any enabled source's data changes — tracked via a per-campaign `effective_catalog_version` counter bumped on either event): for each `(template_key)` identity-group across all objects visible to the campaign (i.e. `overrides_object_id`/`replaces_object_id` chains), pick the object belonging to the highest-precedence enabled source in the chain; merge `patch` fields over the base object's fields for `override` (not `replaces`) relations; objects from disabled/not-enabled sources are excluded entirely from the effective view (but their rows, and any campaign state referencing them, are retained — `FR-CMP-3`, shown as "inactive").
4. **No cross-source template_key collisions required.** Base-game and an unrelated Workshop mod can both use `template_key = "LaserRifle"` in their own `source_id` scope without conflict — identity is `(source_id, template_key)`; only explicit `overrides_object_id`/`replaces_object_id` links (authored by the overriding pack, not inferred by name) create precedence relationships. This directly satisfies "template names must not be assumed globally unique."

## Consequences
- LWotC-style total conversions are modeled as a source with `kind='total_conversion'` whose pack declares explicit overrides of the base-game objects it changes; the base pack is untouched and still importable/updatable independently.
- The effective-catalog function is the single place precedence logic lives; planner, browser and requirement resolver all consume its output rather than re-implementing "which one wins."
- Caching is required for performance (NFR-5) and must be invalidated correctly — covered by `tests/test_effective_catalog.py` (enable/disable a source, re-import a pack, assert cache invalidation).

## Alternatives considered
- Implicit precedence by matching `template_name` across sources: rejected — the brief explicitly warns template IDs are not globally unique between unrelated sources; implicit name-matching would create false overrides between coincidentally-same-named objects from unrelated mods.
- Resolving precedence at import time by mutating the base object: rejected — would make imports non-idempotent/non-reversible and destroy the ability to disable the overriding source later and fall back to base behaviour.
