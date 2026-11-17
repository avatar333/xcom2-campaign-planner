# ADR-0004: Generic polymorphic relationship model

## Status
Accepted

## Context
XCOM 2 progression is not a simple tree: a tech can require multiple other techs AND an item AND a resource amount; a weapon can have alternative upgrade paths (OR groups); facilities require prior facilities and resources; LWotC adds entirely new relationship kinds (e.g. covert-action requirements). Hard-coding tables like `tech_prerequisites`, `weapon_upgrades` would multiply per relationship kind and break the "no code change for new content" requirement.

## Decision
One table, `catalog_relationships`, with columns `(id, relationship_type_id, source_object_id, target_object_id, group_key, group_logic, amount, unit, sort_order, source_pack_id, is_user_authored, attributes JSON)`. `catalog_relationship_types` is a data table (seeded with: `prerequisite`, `unlocks`, `upgrades_to`, `requires_facility`, `produced_by`, `requires_resource`, `class_can_equip`, `compatible_with`, `replaces`, `supersedes`, `requires_research`, and extensible by packs) with an `inverse_type_id` (self-referencing) so the UI can show inbound/outbound edges symmetrically (e.g. `unlocks` is the inverse of `prerequisite`).

**AND/OR semantics**: requirements for a single target that must all hold use distinct rows sharing no `group_key` (implicit AND). Alternative/either-or requirements share the same `(target_object_id, relationship_type_id, group_key)` with `group_logic = 'OR'`; the resolver (ARCHITECTURE.md §7) requires at least one satisfied member of each OR group and all rows outside any group.

`amount`/`unit` cover quantities (e.g. `requires-resource` → 1 Elerium Core), left NULL for boolean relationships.

## Consequences
- Any new kind of relationship (e.g. LWotC's "requires Covert Action completed") is a new row in `catalog_relationship_types`, zero code changes.
- The requirement resolver (`app/domain/requirements.py`) is a single generic graph-walk, testable once against synthetic fixtures, not once per relationship kind.
- Query cost of "what does X require" is a single indexed lookup on `source_object_id`/`target_object_id`; cycles are tolerated (self-referencing graph) but the resolver must guard against infinite recursion (visited-set) since `supersedes`/`upgrades_to` chains plus modded content could in principle cycle.

### Addendum (Phase 3 implementation, `app/services/catalog_relationships.py`)
Relationships are not globally assumed acyclic, consistent with the above — but a small, explicitly-named subset of relationship *kinds* whose semantics inherently cannot be satisfied if cyclic (`prerequisite`, `requires_facility`, `requires_research`) are guarded at creation time: the service layer rejects creating one of these relationship types in a direction that would form a **direct two-node cycle** (A requires B, then B requires A) with a clear validation error. This is a narrow, UI-time sanity check, not a graph-wide cycle detector — longer cycles (A→B→C→A) are not currently detected and remain the responsibility of the future requirement resolver's `visited`-set guard (which treats any cycle it encounters as `unknown`/unresolved, per the main decision above, and `importer/validator.py` is expected to flag true cycles as import-time warnings in a later phase). Self-relations (an object relating to itself) are rejected for every relationship kind, not just the acyclic-required ones.

## Alternatives considered
- A typed table per relationship kind: rejected, same extensibility problem as per-category tables (ADR-0003).
- Storing requirements as an expression tree/string (e.g. a mini boolean DSL) in a single JSON blob per object: rejected — much harder to query ("what requires X"), to validate referential integrity (FKs to objects), and to author by hand in packs; the explicit AND rows / OR `group_key` achieves the same expressiveness relationally.
- Full graph-wide cycle detection (DFS across the whole relationship graph) on every relationship creation: rejected for this phase as disproportionate — most mod content is small, cycles longer than two nodes are rare authoring mistakes rather than a UX hazard worth blocking on, and the future requirement resolver already tolerates and safely handles cycles of any length via its visited-set guard.
