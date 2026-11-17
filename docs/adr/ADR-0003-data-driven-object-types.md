# ADR-0003: Data-driven object types (no schema change per category)

## Status
Accepted

## Context
The game has dozens of object categories (weapons, armour, research, facilities, soldier classes, enemies, missions, ...) and mods routinely add entirely new categories (a new resource type, a new soldier class, a "covert action" concept in LWotC). The brief explicitly forbids hard-coding XCOM content into Python and requires that new object types be added without destructive schema redesign.

## Decision
`catalog_object_types` is a data table (seeded with the categories in `DOMAIN_MODEL.md` §4, extensible by any content pack). `catalog_objects` has one normalized core (id, type, source, name, slug, short/long description, template_name, tags, status, timestamps) that applies to *every* type, plus a single validated JSON `attributes` column for type-specific fields (damage, aim, tier, upkeep cost, science/engineering score, ...). Per-type attribute *schemas* are declared in `catalog_attribute_definitions` (key, data type, unit, required?, default, owning type) so the UI can render/validate forms generically and a pack can introduce new attribute keys for new or existing types without a migration.

Python contains:
- generic CRUD/query code over `catalog_objects` filtered by `type_id`,
- generic relationship traversal,
- a generic attribute-schema-driven form renderer,

and contains **no** `if type == "weapon"` branches encoding game knowledge (tier thresholds, costs, which attribute means what) — those live in `catalog_attribute_definitions` rows and pack data.

## Consequences
- Adding "Covert Action" as a new LWotC object type is a pack-data change (`object_types` + `attribute_definitions` + objects), not a code change.
- Reporting/validation of attributes happens at import/edit time against `catalog_attribute_definitions`, not via static Python schemas (see ADR-0012 for the JSON-vs-EAV tradeoff this implies).
- Some UI type-specific layout (e.g. "weapons show a damage badge") is allowed to key off *generic, data-described* hints (an `attribute_definitions.display_hint` column) rather than the type's identity, keeping even presentation logic content-agnostic where practical. A small, documented exception is permitted for the generic *icon-by-category* fallback (ADR-0006) and for ordering object types in navigation — these use the type's `slug`/`sort_order` columns, still data, not literal game knowledge.

## Alternatives considered
- One table per object type (`weapons`, `armours`, `research`, ...): rejected — exactly the "destructive schema redesign per new category" the brief forbids; mods could never add a type.
- Full EAV (entity-attribute-value, one row per attribute): considered and rejected in favour of a JSON column, see ADR-0012.
