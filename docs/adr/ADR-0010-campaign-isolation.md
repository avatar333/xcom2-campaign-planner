# ADR-0010: Campaign isolation strategy

## Status
Accepted

## Context
The product requires that creating, editing or deleting one campaign can never mutate another campaign's state or the shared catalog. This must be true even under future feature growth (soldiers, plans, events, loadouts, ...).

## Decision
Structural rules enforced by schema + a thin service layer, not by application-level discipline alone:

1. Every campaign-scoped table has a `campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE`, is indexed on it, and **no campaign-scoped table is ever referenced by a non-campaign-scoped table** (catalog tables have zero knowledge of campaigns — enforced by code review / a schema lint test that asserts no FK points from `catalog_*` to `campaigns`).
2. All reads/writes of campaign data go through `app/repositories/campaign_*.py` functions that take `campaign_id` as an explicit, mandatory first argument and always include it in the `WHERE`/`JOIN` clause — there is no "get object state by state_id alone" code path reachable from a route without the owning campaign id also being checked, which also closes an IDOR-style cross-campaign access bug class.
3. `campaign_object_state`'s natural key is `(campaign_id, catalog_object_id)`; it is never looked up by its surrogate `id` alone from a route handler.
4. Deletion is a single `DELETE FROM campaigns WHERE id = ?` relying on `ON DELETE CASCADE` through the FK graph (verified by `tests/test_campaign_isolation.py`: create two campaigns with overlapping catalog references, mutate/delete one, assert byte-for-byte the other campaign's rows and the catalog are unchanged).
5. Duplication (`FR-CMP-4`) is an explicit service function that deep-copies rows with a new `campaign_id` and remapped surrogate keys (e.g. old plan-item id → new plan-item id) — never a shared row.

## Consequences
- Isolation is testable mechanically (cascade + lint test) rather than relying on every future feature remembering to filter by campaign.
- The service-layer convention (`campaign_id` mandatory first arg) is a code-review checklist item and can be enforced with a simple static check if desired later.

## Alternatives considered
- Soft-delete only, application-level filtering by campaign in every query: rejected as weaker — a missed `WHERE` clause anywhere becomes a cross-campaign leak; FK cascade + mandatory-argument repositories make the common mistake structurally impossible rather than merely discouraged.
