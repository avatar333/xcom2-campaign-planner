"""Dashboard routes. Global landing page + campaign-specific dashboard
(UI_UX_SPEC.md §3.1). Full dashboard widgets (priorities/blocked/events)
arrive with Planning (Phase 7); this phase renders the campaign header,
enabled-sources summary and empty states.
"""
from __future__ import annotations

from flask import Blueprint, abort, render_template

from app.repositories import campaigns as campaigns_repo
from app.services.campaign_context import set_active_campaign

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def index():
    campaigns = campaigns_repo.list_campaigns()
    return render_template("dashboard/index.html", campaigns=campaigns)


@dashboard_bp.route("/campaigns/<int:campaign_id>/dashboard")
def campaign_dashboard(campaign_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)
    set_active_campaign(campaign.id)
    return render_template("dashboard/campaign_dashboard.html", campaign=campaign)
