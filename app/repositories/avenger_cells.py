"""Campaign Avenger-grid cell repository (ADR-0010, ADR-0020).

Every function takes `campaign_id` as its mandatory first argument and
filters by it, per the campaign-isolation convention.
"""
from __future__ import annotations

from app.domain.avenger_grid import all_cells
from app.extensions import db
from app.models import CampaignAvengerCell


def list_cells(campaign_id: int) -> list[CampaignAvengerCell]:
    return (
        CampaignAvengerCell.query.filter_by(campaign_id=campaign_id)
        .order_by(CampaignAvengerCell.grid_row.asc(), CampaignAvengerCell.grid_column.asc())
        .all()
    )


def get_cell(campaign_id: int, coordinate: str) -> CampaignAvengerCell | None:
    return CampaignAvengerCell.query.filter_by(
        campaign_id=campaign_id, coordinate=coordinate
    ).first()


def ensure_cells(campaign_id: int) -> int:
    """Create any of the 12 cells that do not exist yet for this campaign.
    Idempotent; returns the number of cells created."""
    existing = {cell.coordinate for cell in list_cells(campaign_id)}
    created = 0
    for row, column, coordinate in all_cells():
        if coordinate in existing:
            continue
        db.session.add(
            CampaignAvengerCell(
                campaign_id=campaign_id,
                coordinate=coordinate,
                grid_row=row,
                grid_column=column,
                build_state="none",
            )
        )
        created += 1
    if created:
        db.session.flush()
    return created


def cells_using_facility_ids(campaign_id: int, facility_object_ids: set[int]) -> list[CampaignAvengerCell]:
    if not facility_object_ids:
        return []
    return CampaignAvengerCell.query.filter(
        CampaignAvengerCell.campaign_id == campaign_id,
        CampaignAvengerCell.facility_object_id.in_(facility_object_ids),
    ).all()


def is_object_used_by_any_cell(object_id: int) -> bool:
    """Catalog-side guard (never a campaign filter): is this catalog object
    referenced as a room state or facility by *any* campaign's grid?"""
    return (
        db.session.query(CampaignAvengerCell.id)
        .filter(
            (CampaignAvengerCell.room_state_object_id == object_id)
            | (CampaignAvengerCell.facility_object_id == object_id)
        )
        .first()
        is not None
    )
