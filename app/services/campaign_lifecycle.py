"""Campaign lifecycle service (ARCHITECTURE.md §2, ADR-0010, FR-CMP-1..6).

Kept as a service (not inlined in the route) because campaign creation also
needs to pre-select a game profile's default content sources, and deletion
is a single structural operation (`ADR-0010`: one `DELETE FROM campaigns`,
cascading through FKs) that deserves one, auditable call site.
"""
from __future__ import annotations

from app.extensions import db
from app.models import Campaign, CampaignContentSource
from app.repositories import avenger_cells as avenger_cells_repo
from app.repositories import campaigns as campaigns_repo
from app.repositories import content_sources as content_sources_repo
from app.services.game_profiles import get_game_profile


class CampaignValidationError(ValueError):
    """Raised when campaign input fails validation (e.g. blank name)."""


def _validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise CampaignValidationError("Campaign name is required.")
    if len(name) > 200:
        raise CampaignValidationError("Campaign name must be 200 characters or fewer.")
    return name


def create_campaign(
    *,
    name: str,
    game_profile_slug: str | None,
    difficulty: str = "commander",
    is_ironman: bool = False,
    notes: str | None = None,
) -> Campaign:
    name = _validate_name(name)

    campaign = Campaign(
        name=name,
        game_profile_slug=game_profile_slug or None,
        difficulty=difficulty,
        is_ironman=bool(is_ironman),
        notes=(notes or "").strip() or None,
    )
    db.session.add(campaign)
    db.session.flush()  # assign campaign.id before attaching content sources

    if game_profile_slug:
        profile = get_game_profile(game_profile_slug)
        if profile is not None:
            for precedence, slug in enumerate(profile.default_sources):
                source = content_sources_repo.get_content_source_by_slug(slug)
                if source is not None:
                    db.session.add(
                        CampaignContentSource(
                            campaign_id=campaign.id,
                            content_source_id=source.id,
                            enabled=True,
                            precedence=precedence,
                        )
                    )

    # Every campaign owns its own 12-cell Avenger grid from the start (ADR-0020).
    avenger_cells_repo.ensure_cells(campaign.id)

    db.session.commit()
    return campaign


def update_campaign(
    campaign: Campaign,
    *,
    name: str,
    difficulty: str = "commander",
    is_ironman: bool = False,
    notes: str | None = None,
) -> Campaign:
    name = _validate_name(name)
    return campaigns_repo.update_campaign(
        campaign,
        name=name,
        difficulty=difficulty,
        is_ironman=bool(is_ironman),
        notes=(notes or "").strip() or None,
    )


def archive_campaign(campaign: Campaign) -> Campaign:
    return campaigns_repo.update_campaign(campaign, status="archived")


def reactivate_campaign(campaign: Campaign) -> Campaign:
    return campaigns_repo.update_campaign(campaign, status="active")


def delete_campaign(campaign: Campaign) -> None:
    """Delete exactly one campaign and its own rows.

    Relies on `ON DELETE CASCADE` declared on every `campaign_*` FK
    (ADR-0010) -- the catalog and every other campaign are structurally
    unreachable from this call.
    """
    campaigns_repo.delete_campaign(campaign)
