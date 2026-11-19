"""Campaign <-> content-source association service (FR-CMP-2/3).

Enforces the `conflicts` declaration from `content_source_relations`
(`ARCHITECTURE.md` §5.4: "conflicts ... hard-checked at campaign
source-enable time") and keeps the `effective_catalog_version` bump
(cache-invalidation trigger, `ARCHITECTURE.md` §7.3) inside the repository
layer so every mutation path goes through it.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import joinedload

from app.models import Campaign, CampaignContentSource, ContentSource
from app.repositories import campaign_sources as repo
from app.repositories import content_sources as sources_repo


@dataclass(frozen=True)
class CampaignSourceView:
    """One content source as attached to one campaign: the persisted
    association (precedence, enabled) together with the real source (name,
    kind). Every screen that displays a campaign's sources uses this, so they
    can never disagree about which sources a campaign has."""

    association: CampaignContentSource
    source: ContentSource

    @property
    def name(self) -> str:
        return self.source.name

    @property
    def slug(self) -> str:
        return self.source.slug

    @property
    def kind(self) -> str:
        return self.source.kind

    @property
    def precedence(self) -> int:
        return self.association.precedence

    @property
    def enabled(self) -> bool:
        return bool(self.association.enabled)


def list_campaign_sources(campaign_id: int) -> list[CampaignSourceView]:
    """The campaign's actual persisted source associations -- the single source
    of truth -- in the explicit precedence order, with the source names
    resolved. Nothing is derived from the campaign's game profile: the profile
    only seeded these rows at creation, and the user may have changed them."""
    associations = (
        CampaignContentSource.query.options(joinedload(CampaignContentSource.content_source))
        .filter_by(campaign_id=campaign_id)
        .order_by(CampaignContentSource.precedence.asc(), CampaignContentSource.id.asc())
        .all()
    )
    return [CampaignSourceView(a, a.content_source) for a in associations]


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
