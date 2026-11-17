# ADR-0012: Typed attributes via validated JSON column, not full EAV tables

## Status
Accepted

## Context
Object types need source-specific attributes of varying shape (a weapon has `tier`, `damage`, `crit`, `clip_size`; a research project has `science_score`, `credit_cost`; a facility has `power_cost`, `grid_slots`). The brief asks for "normalized fields for common information and extensible structured metadata where source-specific attributes differ," and warns against "a single giant JSON blob as a substitute for a sensible relational model."

## Decision
Hybrid:
- **Normalized columns** on `catalog_objects` for information common to *every* object regardless of type: id, type, source, name, slug, short_description, long_description, template_name, tags (normalized as `catalog_object_tags` join table, not JSON, since tags are filtered/searched), status/is_retired, is_user_authored, timestamps, primary asset reference.
- **One JSON column** (`attributes`) for the genuinely type-varying leftover, but it is not "giant" or unstructured: every key that can appear in it for a given type must be pre-declared in `catalog_attribute_definitions (object_type_id, key, data_type, unit, is_required, default_value, display_hint, sort_order)`. Import and edit-form validation reject unknown keys or wrong-typed values against this schema. Attributes that need to be relationally queried/filtered efficiently and are common across many types (e.g. "tier"/"rank" for ordering, "cost" expressed as structured resource amounts) are promoted out of JSON into first-class rows: tiering uses a normalized `tier INTEGER` column on `catalog_objects` (nullable, generic "progression tier" concept, not weapon-specific) and costs are `requires-resource` relationships (ADR-0004), not JSON fields — exactly the "use JSON only where flexibility genuinely helps" guidance. SQLite generated columns / expression indexes (`CREATE INDEX ... (json_extract(attributes, '$.key'))`) are used for any attribute that does need to be queried quickly despite living in JSON.

## Consequences
- Common cross-type operations (search by name/tag/tier/source, relationship traversal, state tracking) are plain relational SQL with indexes.
- Type-specific numeric/text facts stay flexible and pack-extensible without a migration per new attribute.
- A little more validation code (schema-checking JSON against `catalog_attribute_definitions` on write) is required, but it is generic (one validator, data-driven), not per-type.

## Alternatives considered
- Full EAV (`catalog_object_attributes(object_id, key, value)` long/narrow table): rejected — correct in theory but materially worse ergonomics in SQLite for typed values (every value becomes a string needing re-casting), worse for the "edit this object as a form" UI case (would need N row round-trips vs. one JSON blob), and not meaningfully more queryable than JSON1 expression indexes for this app's scale (NFR-5: ≤20k objects).
- Single JSON blob covering *everything including name/type*: rejected directly by the brief's explicit guidance.
