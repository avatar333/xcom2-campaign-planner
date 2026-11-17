"""Effective-catalog resolution: source precedence + override/patch
merging (ADR-0013, `ARCHITECTURE.md` §7).

Pure function over plain dicts -- no ORM, no session, no I/O. The caller
(`app/services/effective_catalog.py`) is responsible for loading rows from
the DB into the shape expected here and for caching the result keyed by
`(campaign_id, campaigns.effective_catalog_version)`.

Expected object shape (one dict per `catalog_objects` row):
    {
        "id": int,
        "source_id": int,
        "overrides_object_id": int | None,
        "replaces_object_id": int | None,
        "patch": dict | None,          # already json.loads'd
        "attributes": dict,            # already json.loads'd
        ... any other fields (name, template_key, slug, type_id, ...)
    }
"""
from __future__ import annotations

from typing import Any

ObjectDict = dict[str, Any]


def _find_root(
    object_id: int, by_id: dict[int, ObjectDict], cache: dict[int, int], _depth: int = 0
) -> int:
    if object_id in cache:
        return cache[object_id]
    if _depth > 64:  # malformed/cyclic override chain guard
        cache[object_id] = object_id
        return object_id

    obj = by_id.get(object_id)
    parent_id = None
    if obj is not None:
        parent_id = obj.get("overrides_object_id") or obj.get("replaces_object_id")

    if parent_id is None or parent_id not in by_id or parent_id == object_id:
        cache[object_id] = object_id
        return object_id

    root = _find_root(parent_id, by_id, cache, _depth + 1)
    cache[object_id] = root
    return root


def _effective_fields(
    object_id: int, by_id: dict[int, ObjectDict], _visited: frozenset[int] = frozenset()
) -> ObjectDict:
    obj = by_id[object_id]
    if object_id in _visited:
        # Cyclic override chain in malformed data -- fall back to the
        # object's own fields rather than recursing forever.
        return dict(obj)
    visited = _visited | {object_id}

    replaces_id = obj.get("replaces_object_id")
    if replaces_id is not None and replaces_id in by_id:
        # Full replacement: no field inheritance from the replaced object.
        return dict(obj)

    overrides_id = obj.get("overrides_object_id")
    if overrides_id is not None and overrides_id in by_id:
        base_fields = _effective_fields(overrides_id, by_id, visited)
        patch = obj.get("patch") or {}
        merged = dict(base_fields)
        for key, value in patch.items():
            if key == "attributes" and isinstance(value, dict):
                merged_attributes = dict(merged.get("attributes") or {})
                merged_attributes.update(value)
                merged["attributes"] = merged_attributes
            else:
                merged[key] = value
        # Identity/provenance fields always reflect the winning (overriding)
        # object itself, never the base it patched -- the base is "who this
        # replaces", not "who this is".
        for identity_key in ("id", "source_id", "template_key", "slug", "overrides_object_id"):
            if identity_key in obj:
                merged[identity_key] = obj[identity_key]
        return merged

    return dict(obj)


def resolve_effective_catalog(
    objects: list[ObjectDict], enabled_source_precedence: dict[int, int]
) -> dict[int, ObjectDict]:
    """Resolve the set of catalog objects visible to a campaign.

    `enabled_source_precedence` maps `source_id -> precedence` for the
    campaign's *enabled* sources only (disabled/unassociated sources must
    be omitted by the caller). Higher precedence number overrides lower
    (`DATABASE_SCHEMA.md` §3.2: "lower precedence number loads first, higher
    overrides").

    Returns `{winning_object_id: effective_fields}` -- one entry per
    identity group that has at least one enabled-source member. Groups with
    no enabled member contribute nothing (object invisible to this
    campaign, `ARCHITECTURE.md` §7.2 step 3).
    """
    by_id: dict[int, ObjectDict] = {obj["id"]: obj for obj in objects}
    root_cache: dict[int, int] = {}
    groups: dict[int, list[int]] = {}

    for object_id in by_id:
        root = _find_root(object_id, by_id, root_cache)
        groups.setdefault(root, []).append(object_id)

    effective: dict[int, ObjectDict] = {}
    for members in groups.values():
        enabled_members = [m for m in members if by_id[m]["source_id"] in enabled_source_precedence]
        if not enabled_members:
            continue
        winner = max(
            enabled_members,
            key=lambda member_id: enabled_source_precedence[by_id[member_id]["source_id"]],
        )
        effective[winner] = _effective_fields(winner, by_id)

    return effective
