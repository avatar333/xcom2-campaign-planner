"""Campaign CRUD routes (FR-CMP-1..6, ADR-0010).

Thin routes: parse request -> call service -> render (ARCHITECTURE.md §2).
Create/Edit/Delete are implemented as modals that work standalone (full
page, for no-JS) and as htmx partial swaps (ADR-0015) -- the same route
serves both, switching template by the `HX-Request` header.

This module also carries campaign <-> content-source association routes
(FR-CMP-2/3): enabling/disabling/reordering sources never deletes campaign
state, only changes what is *visible* (ADR-0013's effective-catalog view).
"""
from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, url_for

from app.forms import CampaignForm
from app.repositories import campaign_sources as campaign_sources_repo
from app.repositories import campaigns as campaigns_repo
from app.repositories import content_sources as sources_repo
from app.services import campaign_lifecycle
from app.services import campaign_sources as campaign_sources_service
from app.services.campaign_context import get_active_campaign_id, set_active_campaign
from app.services.campaign_lifecycle import CampaignValidationError
from app.services.campaign_sources import CampaignSourceError
from app.services.game_profiles import list_game_profiles

campaigns_bp = Blueprint("campaigns", __name__, url_prefix="/campaigns")


def _profile_choices() -> list[tuple[str, str]]:
    choices = [("", "No profile (choose sources later)")]
    choices += [(p.slug, p.name) for p in list_game_profiles()]
    return choices


def _build_form(*args, **kwargs) -> CampaignForm:
    form = CampaignForm(*args, **kwargs)
    form.game_profile_slug.choices = _profile_choices()
    return form


def _is_htmx() -> bool:
    return request.headers.get("HX-Request") == "true"


@campaigns_bp.route("/")
def list_campaigns():
    campaigns = campaigns_repo.list_campaigns()
    active_id = get_active_campaign_id()
    return render_template(
        "campaigns/list.html", campaigns=campaigns, active_campaign_id=active_id
    )


@campaigns_bp.route("/new", methods=["GET", "POST"])
def new_campaign():
    form = _build_form()
    if form.validate_on_submit():
        try:
            campaign = campaign_lifecycle.create_campaign(
                name=form.name.data,
                game_profile_slug=form.game_profile_slug.data,
                difficulty=form.difficulty.data,
                is_ironman=form.is_ironman.data,
                notes=form.notes.data,
            )
        except CampaignValidationError as exc:
            form.name.errors.append(str(exc))
        else:
            set_active_campaign(campaign.id)
            if _is_htmx():
                response = redirect(url_for("campaigns.list_campaigns"))
                response.headers["HX-Redirect"] = url_for("campaigns.list_campaigns")
                return response
            return redirect(url_for("campaigns.list_campaigns"))

    template = "campaigns/_form_modal.html" if _is_htmx() else "campaigns/new.html"
    return render_template(template, form=form, mode="create", campaign=None)


@campaigns_bp.route("/<int:campaign_id>/edit", methods=["GET", "POST"])
def edit_campaign(campaign_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)

    form = _build_form(obj=campaign)
    if request.method == "GET":
        form.game_profile_slug.data = campaign.game_profile_slug

    if form.validate_on_submit():
        try:
            campaign_lifecycle.update_campaign(
                campaign,
                name=form.name.data,
                difficulty=form.difficulty.data,
                is_ironman=form.is_ironman.data,
                notes=form.notes.data,
            )
        except CampaignValidationError as exc:
            form.name.errors.append(str(exc))
        else:
            if _is_htmx():
                response = redirect(url_for("campaigns.list_campaigns"))
                response.headers["HX-Redirect"] = url_for("campaigns.list_campaigns")
                return response
            return redirect(url_for("campaigns.list_campaigns"))

    template = "campaigns/_form_modal.html" if _is_htmx() else "campaigns/edit.html"
    return render_template(template, form=form, mode="edit", campaign=campaign)


@campaigns_bp.route("/<int:campaign_id>/delete", methods=["GET", "POST"])
def delete_campaign(campaign_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)

    if request.method == "POST":
        confirmed_name = (request.form.get("confirm_name") or "").strip()
        if confirmed_name != campaign.name:
            return render_template(
                "campaigns/_delete_modal.html",
                campaign=campaign,
                error="Typed name does not match. Deletion cancelled.",
            ), 400

        was_active = get_active_campaign_id() == campaign.id
        campaign_lifecycle.delete_campaign(campaign)
        if was_active:
            set_active_campaign(None)

        if _is_htmx():
            response = redirect(url_for("campaigns.list_campaigns"))
            response.headers["HX-Redirect"] = url_for("campaigns.list_campaigns")
            return response
        return redirect(url_for("campaigns.list_campaigns"))

    template = "campaigns/_delete_modal.html" if _is_htmx() else "campaigns/delete.html"
    return render_template(template, campaign=campaign, error=None)


@campaigns_bp.route("/<int:campaign_id>/select", methods=["POST"])
def select_campaign(campaign_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)
    set_active_campaign(campaign.id)
    next_url = request.form.get("next") or url_for(
        "dashboard.campaign_dashboard", campaign_id=campaign.id
    )
    return redirect(next_url)


@campaigns_bp.route("/<int:campaign_id>/archive", methods=["POST"])
def archive_campaign(campaign_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)
    campaign_lifecycle.archive_campaign(campaign)
    if get_active_campaign_id() == campaign.id:
        set_active_campaign(None)
    return redirect(url_for("campaigns.list_campaigns"))


@campaigns_bp.route("/<int:campaign_id>/sources")
def manage_sources(campaign_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)
    associations = campaign_sources_repo.list_for_campaign(campaign_id)
    attached_sources = [
        (assoc, sources_repo.get_content_source(assoc.content_source_id)) for assoc in associations
    ]
    available_sources = campaign_sources_repo.available_sources_to_add(campaign_id)
    return render_template(
        "campaigns/sources.html",
        campaign=campaign,
        attached_sources=attached_sources,
        available_sources=available_sources,
        error=request.args.get("error"),
    )


@campaigns_bp.route("/<int:campaign_id>/sources/add", methods=["POST"])
def add_source(campaign_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)
    source_id = request.form.get("content_source_id", type=int)
    source = sources_repo.get_content_source(source_id) if source_id else None
    if source is None:
        abort(400)
    try:
        campaign_sources_service.add_source_to_campaign(campaign, source)
    except CampaignSourceError as exc:
        return redirect(url_for("campaigns.manage_sources", campaign_id=campaign_id, error=str(exc)))
    return redirect(url_for("campaigns.manage_sources", campaign_id=campaign_id))


@campaigns_bp.route("/<int:campaign_id>/sources/<int:source_id>/remove", methods=["POST"])
def remove_source(campaign_id: int, source_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)
    source = sources_repo.get_content_source(source_id)
    if source is None:
        abort(404)
    campaign_sources_service.remove_source_from_campaign(campaign, source)
    return redirect(url_for("campaigns.manage_sources", campaign_id=campaign_id))


@campaigns_bp.route("/<int:campaign_id>/sources/<int:source_id>/toggle", methods=["POST"])
def toggle_source(campaign_id: int, source_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)
    source = sources_repo.get_content_source(source_id)
    if source is None:
        abort(404)
    association = campaign_sources_repo.get_association(campaign_id, source_id)
    if association is None:
        abort(404)
    campaign_sources_service.set_source_enabled(campaign, source, not association.enabled)
    return redirect(url_for("campaigns.manage_sources", campaign_id=campaign_id))


@campaigns_bp.route("/<int:campaign_id>/sources/reorder", methods=["POST"])
def reorder_sources(campaign_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)
    ordered_ids = [int(v) for v in request.form.getlist("content_source_id")]
    campaign_sources_service.reorder_sources(campaign, ordered_ids)
    return redirect(url_for("campaigns.manage_sources", campaign_id=campaign_id))
