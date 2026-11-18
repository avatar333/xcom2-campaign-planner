# ADR-0020: Campaign Avenger grid — separate room state, facility assignment and build lifecycle

## Status
Accepted (Phase 5)

## Context
The Avenger has a fixed construction area of 3 columns × 4 rows (12 cells). A player's real game starts with a *random* arrangement of cleared cells and hazards (Alien Debris, Alien Machinery, Exposed Power Coils), then builds facilities into cleared cells over many weeks. The planner must let each campaign (a) record the real starting layout, (b) plan what to build where, and (c) track what is actually under construction/built — without ever hard-coding facility lists, and without letting one campaign's grid touch another's.

Three tempting shortcuts were rejected up front:
- Encoding everything into one "facility name" field (`"Exposed Power Coil + Power Relay (planned)"`) — makes validation, filtering and future "which facility goes on a coil?" queries impossible.
- Storing position as display order — the grid shape is part of the game's rules; a coordinate is data, not presentation.
- Hard-coding the facility or room list in Python/JS/templates — violates `ADR-0003` (no XCOM content in code).

## Decision

### Table
One campaign-scoped table, `campaign_avenger_cells`, exactly 12 rows per campaign (`DATABASE_SCHEMA.md` §3.10):

| Concept | Column | Meaning |
|---|---|---|
| Position | `coordinate` (`R<row>C<col>`), `grid_row` (1-4), `grid_column` (1-3) | Stable identity. `UNIQUE(campaign_id, coordinate)` and `UNIQUE(campaign_id, grid_row, grid_column)`; a CHECK ties `coordinate` to the two integers. |
| 1. Room / excavation state | `room_state_object_id` → `catalog_objects` (type `avenger_room_state`), NULL = *not configured by the player yet* | What the cell physically is. |
| 2. Facility assignment | `facility_object_id` → `catalog_objects` (type `facility`), NULL = none | Which catalog facility the player has chosen/has here. |
| 3. Facility build state | `build_state` ∈ {`none`,`planned`,`constructing`,`built`} | Lifecycle of that assignment. A CHECK enforces `facility_object_id IS NULL ⇔ build_state = 'none'`. |

All catalog FKs are `ON DELETE RESTRICT`; `campaign_id` is `ON DELETE CASCADE`. No catalog table references this table (`ADR-0010` invariant 1).

### Actual vs planned: one assignment with an explicit lifecycle
We considered separate `current_facility_id` / `planned_facility_id` columns. We chose **a single facility assignment with a lifecycle state** because it is the least confusing normalized model for this game:
- A built XCOM 2 facility cannot be demolished and replaced, so a cell never needs "what exists" *and* an independent "what I wish were here" at once. What exists is `built`/`constructing`; what is intended is `planned`; the same row simply moves along `planned → constructing → built`.
- Two columns would admit contradictory states (current ≠ planned for an unbuilt cell) and require reconciliation rules the player has no mental model for.
- Planning information is never destroyed by recording reality: a `planned` row is kept even when its prerequisites are unmet, and any state can be corrected back (e.g. `built → planned`) when the player mis-clicks.

The UI's **CURRENT** panel shows the facility when `build_state ∈ {constructing, built}`; **PLAN** shows it when `planned`.

### "Available to build" and "Blocked" are derived, not stored
For a `planned` assignment the application evaluates the catalog's factual prerequisites against the campaign (`app/domain/requirements.py`) and the room state, and derives `planned_available` ("Available to build") or `planned_blocked`. They are never persisted, so they cannot go stale when research progress or the ruleset changes. Constructing/built facilities are never shown as blocked: they demonstrably exist in the player's game.

### Everything else comes from the effective catalog
- **Facility list**: effective-catalog objects (`ADR-0013`) of type `facility` with `buildable_in_avenger_grid` true — so ruleset filtering and overrides (e.g. the `wotc` pack removes the vanilla Advanced Warfare Center via a patch override setting `buildable_in_avenger_grid: false`) are pure data.
- **Room-state list**: effective-catalog objects of type `avenger_room_state`. A mod pack can add a new room state with no code change. Room-state attributes `blocks_construction` (falls back to `clearable`) and `is_default_empty` (used by "Reset to EMPTY") are data.
- **Duplicates**: the facility's `allows_duplicates` attribute *as resolved for the campaign's ruleset*. A non-duplicable facility may occupy one cell per campaign. An override can flip this per ruleset.
- **Prerequisites**: inbound `prerequisite`/`unlocks`/`requires_research`/`requires_facility` relationships (grouped by `group_key`) plus compound AND/OR trees (`ADR-0018`) targeting the facility *or any object it overrides* (an override patches rather than copies). Satisfaction = `campaign_object_state.status ∈ {completed, owned}` (or `prerequisite_override`), or, for a facility requirement, a `built` cell holding it.
- **Identity stored**: the *effective winner* object id at assignment time; overridden/base ids are normalized to the winner so the same logical facility is one identity within a ruleset. If a source is later disabled, the cell keeps its row, reads the object directly, and is flagged "not available in this campaign's current content sources" rather than being dropped.

### Campaign-state writes the planner performs
Besides cells, the planner offers "Mark <prerequisite> complete" which upserts `campaign_object_state(campaign_id, catalog_object_id)` (natural key, `ADR-0010`). It is the same row the future state-tracking screens will own; the planner only reads it for evaluation and writes `completed`/`unknown`.

### Grid initialisation
`create_campaign` creates the 12 cells; `get_grid_view` lazily creates any missing cells for campaigns that pre-date this feature (idempotent). All cells start with `room_state_object_id = NULL`: the application never assumes a starting layout.

### Configure Starting Avenger
A client-side draft (Alpine) holds the whole layout; nothing persists until **Save Layout** posts it. The server validates every entry against the campaign's room states first and applies the draft atomically (all-or-nothing). Cells absent from the draft are left unchanged. **Reset to EMPTY** resets the draft only; the server-side reset route exists for API/script use and keeps facility plans unless asked.

## Consequences
- Future questions ("what should I build next?", "which planned facility is blocked and what unlocks it?", "which facility on a power coil?", "will power suffice?") are SQL/domain queries over three independent columns plus the unchanged catalog graph — no parsing of display text.
- The requirement evaluator is a pure generic function (`app/domain/requirements.py`) reusable by the future plan/blocked-ready engine (`ARCHITECTURE.md` §6) for any object type.
- Power is reported from catalog `power_cost` figures only (negative = generation). Avenger base power, engineer staffing and power-coil bonuses are not tracked, so no surplus/shortfall is computed or fabricated.
- A cell on an uncleared hazard room can be *planned* (the player may be about to clear it) but is shown blocked until it is constructing/built or the room state is changed.

## Alternatives considered
- Current + planned facility columns: rejected (contradictory states, no real-game meaning — see above).
- A generic `campaign_object_state` row per facility with the grid position in `state_attributes` JSON: rejected — position and uniqueness are structural and need constraints, indexes and a unique key, which JSON cannot give.
- Storing a derived `blocked` flag: rejected — derived data that silently goes stale (`DOMAIN_MODEL.md` §9.1: availability is never stored as fact).
- Seeding a "default" starting layout: rejected — the player's game is random; guessing would be wrong more often than right.
