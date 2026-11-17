# ADR-0009: Game facts and strategy recommendations are modeled and stored separately

## Status
Accepted

## Context
"What should I research next" has an objective component (what is unlocked, what is blocked, from `catalog_relationships` facts) and a subjective component (community strategy consensus, e.g. "rush Guerrilla Tactics School early"). Conflating them would make opinionated advice look like game rules, and would make it impossible to disable/compare strategies.

## Decision
Facts live only in `catalog_objects`/`catalog_relationships`, sourced from packs with `content_sources.kind` provenance (ADR-0005), and are what the deterministic requirement resolver (`app/domain/requirements.py`) uses — this code path never consults opinion data.

A separate, optional subsystem: `strategy_profiles` (id, name, author, description, content_source_scope) and `strategy_recommendations` (profile_id, subject_object_id or subject_query, recommended_action, rationale, priority_hint, sort_order). A campaign optionally enables zero or more strategy profiles (`campaign_strategy_profiles`). The planner's "Next up" view (`FR-PLN-4`) merges deterministic ready/blocked facts with any enabled profile's `priority_hint` purely as a *sort/annotation* layer, visually tagged (e.g. a distinct badge/colour, see `UI_UX_SPEC.md`) so the user always sees which parts are "the game says so" vs. "this strategy profile suggests".

## Consequences
- A user can toggle off all strategy profiles and still get a fully correct, fact-only planner.
- Multiple competing community strategies (vanilla vs. LWotC meta) can coexist as different profiles without editing game facts.
- Recommendation authors (including future in-app curation or the user themselves) author profiles the same way mods author content: as data, importable via a pack-like JSON file (`strategy_profiles.json`), not code.

## Alternatives considered
- Baking "recommended order" as a field on `catalog_objects` (e.g. `recommended_priority`): rejected — a single global field can't represent competing strategies or per-campaign toggling, and conflates fact with opinion in one row as the brief explicitly warns against.
