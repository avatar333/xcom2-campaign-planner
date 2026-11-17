"""Campaign <-> content-source association service (FR-CMP-2/3).

Enforces the `conflicts` declaration from `content_source_relations`
(`ARCHITECTURE.md` §5.4: "conflicts ... hard-checked at campaign
source-enable time") and keeps the `effective_catalog_version` bump
(cache-invalidation trigger, `ARCHITECTURE.md` §7.3) inside the repository
layer so every mutation path goes through it.
"""
from __future__ import annotations

from app.models import Campaign, ContentSource
from app.repositories import campaign_sources as repo
from app.repositories import content_sources as sources_repo


class CampaignSourceError(ValueError):
    pass


def _conflicting_slugs(source: ContentSource) -> set[str]:
    conflicts = set()
    for relation in sources_repo.list_relations(source):
        if relation.relation == "conflicts":
            related = sources_repo.get_content_source(relation.related_source_id)
            if related is not None:
                conflicts.add(related.slug)
    return conflicts


def add_source_to_campaign(campaign: Campaign, source: ContentSource) -> None:
    if repo.get_association(campaign.id, source.id) is not None:
        raise CampaignSourceError(f"{source.name} is already attached to this campaign.")

    conflicting_slugs = _conflicting_slugs(source)
    if conflicting_slugs:
        enabled_ids = repo.enabled_source_ids(campaign.id)
        enabled_slugs = {
            s.slug for s in (sources_repo.get_content_source(sid) for sid in enabled_ids) if s
        }
        clashing = conflicting_slugs & enabled_slugs
        if clashing:
            raise CampaignSourceError(
                f"{source.name} conflicts with an already-enabled source: {', '.join(sorted(clashing))}."
            )

    repo.add_source(campaign.id, source.id, enabled=True)


def remove_source_from_campaign(campaign: Campaign, source: ContentSource) -> None:
    repo.remove_source(campaign.id, source.id)


def set_source_enabled(campaign: Campaign, source: ContentSource, enabled: bool) -> None:
    repo.set_enabled(campaign.id, source.id, enabled)


def reorder_sources(campaign: Campaign, ordered_source_ids: list[int]) -> None:
    repo.reorder(campaign.id, ordered_source_ids)
