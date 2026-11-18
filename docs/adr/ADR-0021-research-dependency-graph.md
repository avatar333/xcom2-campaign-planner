# ADR-0021: Research queue, completion tracking and the generic dependency graph

## Status
Accepted (Phase 6)

## Context
The player needs to (a) record which research a campaign has finished, (b) plan an ordered queue of what comes next, and (c) pick *any* catalog object (weapon, armour, facility, item, research, Proving Ground project, technology) and see everything required to reach it, with correct AND / OR / nested semantics. The brief forbids a hard-coded research tree: the graph must come from catalog relationships and compound prerequisite expressions (`ADR-0004`, `ADR-0018`), respect the campaign's sources/ruleset (`ADR-0013`), and never mutate catalog definitions (`ADR-0002`).

## Decision

### 1. One requirement expression per object, built from catalog data
`app/services/dependency_index.py` (`DependencyIndex`) turns a campaign's *effective catalog* into plain inputs for a pure domain layer:
- **Relationship vocabulary** read as "source must be satisfied before target": `prerequisite`, `unlocks`, `requires_research`, `requires_facility`, `requires_resource`, `produced_by`. Edges sharing a `group_key` form one group using its `group_logic`; ungrouped edges are separate AND roots. Compound trees (`catalog_prerequisite_groups`) are added as further roots. **Roots are AND-ed; a group is only OR if its `group_logic` is OR.** `A requires B AND C` can therefore never be read as `B OR C`.
- **Ruleset awareness**: only relationships/groups authored by a source *enabled for the campaign* count (`source_pack_id`), so a mod's added prerequisite affects only campaigns that enabled it. An override object (`overrides_object_id`) **inherits** the requirements of what it overrides **unless it declares its own**, in which case its own replace them (LWotC changes Plasma Rifle's requirement rather than adding to it). `replaces_object_id` never inherits.
- **Non-gating requirements**: `requires_resource` (and any `resource`-type object) is *shown* with its amount but never blocks anything — the app does not simulate stockpiles. Facilities *do* gate (a Proving Ground project needs the facility built); they are satisfied by a `built` Avenger cell.
- **Derived objects** (weapons, armour, items, ammo, grenades…) are not actions: they become satisfied when their requirements are met. **Steps** (research, autopsies, Proving Ground projects, technologies, facilities, missions…) are what the player acts on.

### 2. Pure traversal in the domain layer (`app/domain/dependency_graph.py`)
No Flask/SQLAlchemy/I/O. `build_dependency_graph(target, facts, requirements)` returns a **deterministic** graph with: every ancestor at every depth (all OR alternatives included), edges directed *requirement → dependent*, a **gate node** for each OR (`ANY ONE OF`) and each AND nested inside an OR, per-node status, and a `PathAnalysis` (satisfied, missing, actionable now, blocked, steps needed on **every** route in dependency order, steps that belong only to *some* route, unresolved alternatives). Cycles are found with an iterative DFS: the back edge is flagged, never followed, never satisfies anything, and is reported; all analyses are iterative or bounded (a 1,500-deep chain is a test). Satisfaction is a least fixed point, so a cycle with no external support stays unsatisfied.

**Alternative paths are never ranked.** An OR step appears in `optional_steps`; only steps common to every alternative are `mandatory_steps` ("Required steps outstanding" counts only those). This is deliberate: "fewest steps" is a strategy opinion (`ADR-0009`), not a fact.

### 3. Campaign status vocabulary
Recorded progress comes from three campaign-scoped sources, merged by priority completed > in-progress > planned: `campaign_object_state` (`completed`/`owned`, `in_progress`, or `prerequisite_override` = asserted satisfied), the research queue (planned), and the Avenger grid (built/constructing/planned facilities). Displayed status: **completed, in progress, planned, available, locked (blocked)** — "Unknown/Unavailable" in the brief is *locked*. `available` and `locked` are **derived** and never stored (`DOMAIN_MODEL.md` §9.1). Labels adapt to the type (a facility reads "Built / Constructing / Available to build"). The one function `compute_object_statuses` is used by every screen, so a facility planner and the research planner can never disagree.

### 4. Storage
- `campaign_research_queue(campaign_id, catalog_object_id, position)` — `UNIQUE(campaign_id, catalog_object_id)`, `CHECK position >= 0`, campaign `CASCADE`, catalog `RESTRICT`. `position` is the single ordering key and is renumbered `0..n-1` on every write. Membership means *planned or in progress*.
- `campaign_object_state` gains `completed_at` (ISO timestamp) and `completion_sequence` (per-campaign counter, 1,2,3…) so the order of completion survives equal timestamps. Both are NULL unless completed; re-completing keeps the original values; un-completing clears them.
- Completing a project removes it from the queue; removing an in-progress item stops it. All writes go through `app/services/campaign_progress.py`, which the Avenger planner's "mark prerequisite complete" now also uses.
- Catalog rows are only read. Re-importing a pack never touches queue/state rows (they reference stable catalog ids; stale override ids are normalised to the ruleset winner on read).

### 5. Queue validation warns, never prevents
Each queue item is evaluated against *what would be complete by its position*: everything already satisfied plus the queue items **before** it (an in-progress item counts as done only for items after it). Unmet requirements produce warnings like `Blocked before position 3: Advanced Coilguns incomplete`; an unmet OR is phrased as one alternative-set. **Suggest a valid order** is a separate, explicit action: a *stable* topological sort of the same queued projects (an item moves only when something it needs is queued after it; for an OR it depends on the alternative the user already queued earliest). It also lists blockers that no queued project can resolve. Applying it posts exactly the order the user was shown; nothing is reordered silently.

### 6. Graph rendering
Cytoscape.js + dagre (+ cytoscape-dagre), all MIT, vendored under `static/js/vendor/` and loaded **only** on the unlock-path page (`NFR-4`: no CDN). The server embeds the graph as JSON and the browser only draws it; layout orientation is chosen to maximise node size. Status is encoded by **border style + text marker + colour** (solid ✓, double ▶, dashed ○, dotted ✕, target ★) and the page always includes a complete text version (steps, alternatives, satisfied list), so nothing depends on the canvas or on colour.

### 7. Recommendation seam
`RecommendationProvider` / `Recommendation` in `app/services/research.py` is the only place advice can enter. Facts ("Magnetic Weapons requires X") come exclusively from the catalog graph; strategy ("research it early") would come from a provider backed by `strategy_profiles` (`ADR-0009`). The default provider returns nothing, no screen renders recommendations yet, and a test proves installing one does not alter any factual output.

## Consequences
- Any catalog object — including user/mod objects — can be an unlock target with no code change.
- The Avenger planner now evaluates prerequisites through the same index, so there is a single definition of "prerequisite" in the application. The soldier/build phase can reuse `DependencyIndex.ref()`/`get_unlock_path` for "Show Unlock Path" on equipment.
- Resources are displayed but unmetered; there is no stockpile or power simulation.
- `requires_facility` in the shipped packs follows the authoring convention *source = the requirement* (`DOMAIN_MODEL.md` §6 describes the opposite wording; the packs, the Avenger planner and this ADR agree with each other). Phase 6 data additions: Proving Ground projects now list the Proving Ground facility as a requirement.

## Alternatives considered
- Hard-coded research tree / precomputed JSON graph: rejected (brief; would drift from catalog data and ignore rulesets).
- Computing the graph in JavaScript: rejected — the semantics (AND/OR, cycles, overrides, per-campaign progress) must be testable without a browser and shared by every screen.
- Choosing a "best" alternative route (fewest steps/cheapest): rejected as strategy, not fact.
- A new table for per-campaign research state: rejected — `campaign_object_state` already exists for exactly this; only completion order was missing.
- Blocking impossible queue orders: rejected — the player may know about effects the app cannot see; warn instead.
