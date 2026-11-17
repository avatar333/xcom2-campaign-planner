"""Campaign repository (ADR-0010).

`campaigns` itself is the isolation root, so its own lookups are naturally
keyed by `campaign_id`. Every *campaign-scoped* table's repository (added in
later phases: campaign_state, campaign_plans, campaign_soldiers, ...) must
follow the same convention established here: `campaign_id` as the mandatory
first argument of any function that reads/writes rows belonging to one
campaign, and that id always appears in the query's WHERE/filter clause --
never a bare `.get(row_id)` on a campaign-scoped table.
"""
from __future__ import annotations

from app.extensions import db
from app.models import Campaign


def list_campaigns(*, include_archived: bool = True) -> list[Campaign]:
    query = Campaign.query
    if not include_archived:
        query = query.filter(Campaign.status == "active")
    return query.order_by(Campaign.updated_at.desc()).all()


def get_campaign(campaign_id: int) -> Campaign | None:
    return db.session.get(Campaign, campaign_id)


def create_campaign(**fields) -> Campaign:
    campaign = Campaign(**fields)
    db.session.add(campaign)
    db.session.commit()
    return campaign


def update_campaign(campaign: Campaign, **fields) -> Campaign:
    for key, value in fields.items():
        setattr(campaign, key, value)
    db.session.commit()
    return campaign


def delete_campaign(campaign: Campaign) -> None:
    db.session.delete(campaign)
    db.session.commit()
