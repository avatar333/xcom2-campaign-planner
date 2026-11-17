"""Campaign <-> content-source association repository (FR-CMP-2/3, ADR-0010).

Every function takes `campaign_id` as a mandatory first argument and filters
by it, per the campaign-isolation convention established in
`app/repositories/campaigns.py`.
"""
from __future__ import annotations

from app.extensions import db
from app.models import Campaign, CampaignContentSource, ContentSource


def list_for_campaign(campaign_id: int) -> list[CampaignContentSource]:
    return (
        CampaignContentSource.query.filter_by(campaign_id=campaign_id)
        .order_by(CampaignContentSource.precedence.asc())
        .all()
    )


def get_association(campaign_id: int, content_source_id: int) -> CampaignContentSource | None:
    return CampaignContentSource.query.filter_by(
        campaign_id=campaign_id, content_source_id=content_source_id
    ).first()


def enabled_source_ids(campaign_id: int) -> list[int]:
    """Enabled source ids for a campaign, ordered by precedence ascending
    (lower precedence number = loads first; ARCHITECTURE.md §7.2)."""
    rows = (
        CampaignContentSource.query.filter_by(campaign_id=campaign_id, enabled=True)
        .order_by(CampaignContentSource.precedence.asc())
        .all()
    )
    return [row.content_source_id for row in rows]


def _bump_effective_catalog_version(campaign_id: int) -> None:
    campaign = db.session.get(Campaign, campaign_id)
    if campaign is not None:
        campaign.effective_catalog_version += 1


def add_source(
    campaign_id: int, content_source_id: int, *, enabled: bool = True, precedence: int | None = None
) -> CampaignContentSource:
    if precedence is None:
        existing = list_for_campaign(campaign_id)
        precedence = (max((a.precedence for a in existing), default=-1)) + 1

    association = CampaignContentSource(
        campaign_id=campaign_id,
        content_source_id=content_source_id,
        enabled=enabled,
        precedence=precedence,
    )
    db.session.add(association)
    _bump_effective_catalog_version(campaign_id)
    db.session.commit()
    return association


def remove_source(campaign_id: int, content_source_id: int) -> bool:
    association = get_association(campaign_id, content_source_id)
    if association is None:
        return False
    db.session.delete(association)
    _bump_effective_catalog_version(campaign_id)
    db.session.commit()
    return True


def set_enabled(campaign_id: int, content_source_id: int, enabled: bool) -> CampaignContentSource | None:
    association = get_association(campaign_id, content_source_id)
    if association is None:
        return None
    association.enabled = enabled
    _bump_effective_catalog_version(campaign_id)
    db.session.commit()
    return association


def reorder(campaign_id: int, ordered_content_source_ids: list[int]) -> None:
    """Rewrite precedence to match the given order (index 0 = lowest precedence)."""
    for precedence, content_source_id in enumerate(ordered_content_source_ids):
        association = get_association(campaign_id, content_source_id)
        if association is not None:
            association.precedence = precedence
    _bump_effective_catalog_version(campaign_id)
    db.session.commit()


def available_sources_to_add(campaign_id: int) -> list[ContentSource]:
    """Sources not yet associated with this campaign, for the "add source" picker."""
    attached_ids = {a.content_source_id for a in list_for_campaign(campaign_id)}
    all_sources = ContentSource.query.order_by(ContentSource.name.asc()).all()
    return [s for s in all_sources if s.id not in attached_ids]
