"""Effective-catalog service: loads rows, calls the pure
`domain.effective_catalog` resolver, and caches by
`(campaign_id, effective_catalog_version)` (ADR-0013, `ARCHITECTURE.md` §7).
"""
from __future__ import annotations

import json

from app.domain.attributes import parse_attributes_json
from app.domain.effective_catalog import resolve_effective_catalog
from app.extensions import db
from app.models import CatalogObject
from app.repositories import campaign_sources as campaign_sources_repo

# In-process cache: {(campaign_id, version): {object_id: effective_fields}}.
# Bounded and trivially invalidated because the key includes the version
# counter that every mutation path bumps (ARCHITECTURE.md §7.3) -- a stale
# entry is simply never looked up again.
_CACHE: dict[tuple[int, int], dict[int, dict]] = {}
_CACHE_MAX_ENTRIES = 256


def _object_to_dict(obj: CatalogObject) -> dict:
    return {
        "id": obj.id,
        "source_id": obj.source_id,
        "type_id": obj.type_id,
        "template_key": obj.template_key,
        "slug": obj.slug,
        "name": obj.name,
        "short_description": obj.short_description,
        "long_description": obj.long_description,
        "tier": obj.tier,
        "attributes": parse_attributes_json(obj.attributes),
        "primary_asset_id": obj.primary_asset_id,
        "overrides_object_id": obj.overrides_object_id,
        "replaces_object_id": obj.replaces_object_id,
        "patch": json.loads(obj.patch) if obj.patch else None,
        "is_retired": obj.is_retired,
        "is_user_authored": obj.is_user_authored,
    }


def effective_source_ids(campaign_id: int) -> list[int]:
    """Enabled source ids for a campaign (used for simple, non-override-aware
    filtering -- e.g. the catalog search's `source_ids` filter)."""
    return campaign_sources_repo.enabled_source_ids(campaign_id)


def get_effective_catalog(campaign_id: int, *, version: int) -> dict[int, dict]:
    """Return `{object_id: effective_fields}` visible to this campaign.

    `version` must be the campaign's current `effective_catalog_version`;
    callers are responsible for passing the fresh value so the cache key
    naturally invalidates (ARCHITECTURE.md §7.3).
    """
    cache_key = (campaign_id, version)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    enabled_ids = campaign_sources_repo.enabled_source_ids(campaign_id)
    if not enabled_ids:
        _CACHE[cache_key] = {}
        return {}

    precedence_by_source = {
        association.content_source_id: association.precedence
        for association in campaign_sources_repo.list_for_campaign(campaign_id)
        if association.enabled
    }

    # Load every object belonging to an enabled source *or* to a source an
    # enabled object overrides/replaces (so override chains resolve even if
    # the base source itself is disabled -- the chain-walk needs the node,
    # even though a disabled-only node never wins).
    objects = CatalogObject.query.filter(CatalogObject.is_retired.is_(False)).all()
    object_dicts = [_object_to_dict(obj) for obj in objects]

    effective = resolve_effective_catalog(object_dicts, precedence_by_source)

    if len(_CACHE) >= _CACHE_MAX_ENTRIES:
        _CACHE.clear()
    _CACHE[cache_key] = effective
    return effective


def effective_object_ids(campaign_id: int, *, version: int) -> set[int]:
    return set(get_effective_catalog(campaign_id, version=version).keys())


def bump_version_for_sources(source_ids: list[int]) -> None:
    """Invalidate every campaign that has any of these sources enabled
    (called after a future content-pack import; `ARCHITECTURE.md` §7.3)."""
    from app.models import Campaign, CampaignContentSource

    if not source_ids:
        return
    campaign_ids = {
        row.campaign_id
        for row in CampaignContentSource.query.filter(
            CampaignContentSource.content_source_id.in_(source_ids)
        ).all()
    }
    for campaign_id in campaign_ids:
        campaign = db.session.get(Campaign, campaign_id)
        if campaign is not None:
            campaign.effective_catalog_version += 1
    db.session.commit()
