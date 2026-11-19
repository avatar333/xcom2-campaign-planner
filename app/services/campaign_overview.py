"""Campaign dashboard progression summary (UI_UX_SPEC.md section 3.1).

A read-only roll-up of numbers other services already compute, so the dashboard
never invents or duplicates progress logic. Priorities / blocked-plan panels are
not here: they belong to the planning phase.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import Campaign, CampaignSoldier
from app.services import avenger as avenger_service
from app.services import builds as builds_service
from app.services import research as research_service


@dataclass(frozen=True)
class ProgressionSummary:
    research_completed: int
    research_in_progress: int
    research_planned: int
    research_available: int
    research_total: int
    facilities_built: int
    facilities_constructing: int
    facilities_planned: int
    soldiers: int
    builds: int


def progression_summary(campaign: Campaign) -> ProgressionSummary:
    counts = research_service.browse_research(campaign).counts
    grid = avenger_service.get_grid_view(campaign).summary
    return ProgressionSummary(
        research_completed=counts.get("completed", 0),
        research_in_progress=counts.get("in_progress", 0),
        research_planned=counts.get("planned", 0),
        research_available=counts.get("available", 0),
        research_total=sum(counts.values()),
        facilities_built=grid.built,
        facilities_constructing=grid.constructing,
        facilities_planned=grid.planned,
        soldiers=CampaignSoldier.query.filter_by(campaign_id=campaign.id).count(),
        builds=len(builds_service.list_builds(campaign)),
    )
