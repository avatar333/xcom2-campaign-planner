# ADR-0018: Compound (nested AND/OR) prerequisite expression tree

## Status
Accepted (Phase 4)

## Context
`ADR-0004`'s flat `catalog_relationships.group_key`/`group_logic` represents a single level of alternation: several rows sharing one `group_key` form an OR-group, and rows outside any group are implicitly AND-ed. This is sufficient for XCOM 2's real "Plasma Rifle requires (Advanced Lasers OR Advanced Coilguns)" case (one OR group, confirmed against actual game config in `docs/DATA_SOURCING.md`). The Phase 4 brief explicitly asks for deeper nesting to be representable in general — e.g. `A requires (B AND C) OR (D AND E)` — and warns against "a naive single-parent relationship if the factual game requirement is more complex." No concrete curated XCOM 2/WotC/LWotC requirement discovered during this phase's research actually nests two levels deep (every real case found resolves to either a flat AND list or one OR group), but the model must not preclude a future mod from needing it.

## Decision
Add two tables, `catalog_prerequisite_groups` (a tree node: `target_object_id`, `parent_group_id` self-FK, `logic` ∈ {AND, OR}, `relationship_type_id`) and `catalog_prerequisite_group_members` (a leaf pointing at `catalog_object_id`, or a branch pointing at a `child_group_id`, mutually exclusive via a CHECK constraint). A root group (`parent_group_id IS NULL`) is the full requirement tree for `target_object_id`; its members are evaluated by `logic`, where a member is "satisfied" if it is a leaf whose referenced object is satisfied, or a branch whose nested group evaluates to satisfied under the same rule, recursively.

This is **additive, not a replacement** for `ADR-0004`'s flat mechanism: the common case (single-level AND/OR) continues to use `catalog_relationships.group_key` directly, since it is simpler to author and query ("what directly requires X" is one indexed lookup). The tree tables are only populated when a requirement genuinely needs more than one level of nesting — the importer/validator (`docs/DATA_INGESTION.md`) decides which mechanism to use per requirement based on its actual shape, never both for the same requirement.

## Consequences
- The future requirement resolver (`app/domain/requirements.py`, still unbuilt — planning phase) must know how to walk both mechanisms: flat `group_key` rows directly on `catalog_relationships`, and the tree via `catalog_prerequisite_groups`/`_members` for anything deeper. This is a documented, not yet implemented, requirement for that phase.
- No curated data authored in Phase 4 populates these tables (no real nested case was found) — they exist so the schema does not need another migration if/when a mod introduces one, satisfying the brief's "do not implement dependencies using a naive single-parent relationship if the factual game requirement is more complex" instruction as a structural guarantee rather than a promise to revisit later.
- `ON DELETE CASCADE` from parent to child group and from group to its members means deleting a root group cleans up its entire subtree; `ON DELETE RESTRICT` on `catalog_object_id` leaf references preserves the "retire, don't delete, a referenced object" rule from `ADR-0010`/`DATABASE_SCHEMA.md` §7.

## Alternatives considered
- A single `expression` TEXT column holding a serialized boolean expression (e.g. JSON `["OR", ["AND", 12, 13], ["AND", 14, 15]]` with catalog-object ids): rejected — not relationally queryable ("what objects does X require" would need parsing the expression in application code, not SQL), and FK integrity on the referenced object ids would not be enforced by the database.
- Extending `group_key` to accept a delimited/nested key syntax (e.g. `"outer.OR.inner1.AND"`): rejected — ad hoc string encoding of a tree structure is exactly the kind of fragile scheme ADR-0004 already rejected for the single-level case; a proper adjacency-list table is the standard, auditable way to store a tree relationally.
