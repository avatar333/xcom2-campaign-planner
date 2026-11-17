"""Active-campaign context: makes the selected campaign visually/structurally
the current context for campaign-specific screens (UI_UX_SPEC.md §2 top bar).

Stored in the Flask session (single-user, local app -- ADR-0011/PRODUCT_SPEC §2).
"""
from __future__ import annotations

from flask import session

from app.extensions import db
from app.models import Campaign

_SESSION_KEY = "active_campaign_id"


def set_active_campaign(campaign_id: int | None) -> None:
    if campaign_id is None:
        session.pop(_SESSION_KEY, None)
    else:
        session[_SESSION_KEY] = campaign_id


def get_active_campaign_id() -> int | None:
    return session.get(_SESSION_KEY)


def get_active_campaign() -> Campaign | None:
    campaign_id = get_active_campaign_id()
    if campaign_id is None:
        return None
    campaign = db.session.get(Campaign, campaign_id)
    if campaign is None or campaign.status == "archived":
        # Stale/deleted/archived selection -- clear it rather than leak a dangling id.
        set_active_campaign(None)
        return None
    return campaign
