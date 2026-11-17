# ADR-0002: Catalog data vs. campaign state are separate, never merged

## Status
Accepted

## Context
The product must support many independent playthroughs that reference the same shared game content (e.g. "Magnetic Rifle") while tracking per-playthrough progress (researched? owned? quantity?). If campaign-specific fields were added directly to the catalog object row, two campaigns could not hold different values for the same object, and deleting a campaign could risk touching shared data.

## Decision
Two disjoint groups of tables:

1. **Catalog** (`content_sources`, `catalog_object_types`, `catalog_objects`, `catalog_attribute_definitions`, `catalog_relationships`, `catalog_relationship_types`, `assets`) — describes *what exists in the game/mods*. Rows are shared by reference across all campaigns. A catalog row never stores a foreign key to a campaign.
2. **Campaign** (`campaigns`, `campaign_content_sources`, `campaign_object_state`, `campaign_events`, `campaign_plans`, `campaign_plan_items`, `campaign_soldiers`, `campaign_soldier_loadouts`, ...) — describes *what is true in one playthrough*. Every row in this group carries a `campaign_id` foreign key with `ON DELETE CASCADE` scoped to that campaign, and references catalog rows only by their stable internal ID (never copies catalog fields).

`campaign_object_state` is the single join point: `(campaign_id, catalog_object_id)` unique, holding the mutable per-campaign fields (status, quantity, notes, override flags, type-specific JSON state). It is created lazily (no row = default/unknown state), so adding new catalog objects never requires backfilling every campaign.

## Consequences
- Campaign isolation becomes a structural guarantee, not an application convention: a query `DELETE FROM campaigns WHERE id = ?` cascades only inside that campaign's rows.
- Catalog browsing/search code never needs a campaign in scope; campaign dashboards always join catalog + state.
- Slightly more joins at read time; mitigated with indexes (see `DATABASE_SCHEMA.md`) and a per-request "effective catalog" cache (ADR-0013).

## Alternatives considered
- Per-campaign copy-on-write of catalog rows: rejected — duplicates data, complicates pack re-import, and "campaign A's copy of Magnetic Rifle" is not actually a different *object*, only different *state*.
- Single table with a nullable `campaign_id` on catalog objects: rejected — conflates two different lifecycles and would make global catalog rows (`campaign_id IS NULL`) a special case leaking into every query.
