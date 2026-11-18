"""Avenger Facility Planner routes (ADR-0020, FR-PLN-7).

Thin routes (ARCHITECTURE.md §2): every URL carries the campaign id, every
handler loads that campaign first (404 if absent) and calls
`app.services.avenger`, which only ever touches that campaign's own cells.
Modals are htmx fragments that also work as full pages without JS (ADR-0015).
"""
from __future__ import annotations

from flask import Blueprint, abort, make_response, redirect, render_template, request, url_for

from app.domain.avenger_grid import ASSIGNABLE_BUILD_STATES, BUILD_STATE_LABELS
from app.repositories import campaigns as campaigns_repo
from app.services import avenger as avenger_service
from app.services.avenger import AvengerGridError
from app.services.campaign_context import set_active_campaign

avenger_bp = Blueprint("avenger", __name__, url_prefix="/campaigns/<int:campaign_id>/avenger")


def _is_htmx() -> bool:
    return request.headers.get("HX-Request") == "true"


def _load_campaign(campaign_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)
    set_active_campaign(campaign.id)
    return campaign


def _page_url(campaign_id: int, **params) -> str:
    return url_for("avenger.index", campaign_id=campaign_id, **{k: v for k, v in params.items() if v})


def _done(campaign_id: int, **params):
    """Success response: htmx gets a client-side redirect (full reload keeps the
    grid, summary and modal state trivially consistent), plain posts a 302."""
    target = _page_url(campaign_id, **params)
    if _is_htmx():
        response = make_response("", 204)
        response.headers["HX-Redirect"] = target
        return response
    return redirect(target)


def _cell_or_404(campaign, coordinate: str):
    try:
        return avenger_service.get_cell_view(campaign, coordinate)
    except AvengerGridError:
        abort(404)


def _modal_context(campaign, coordinate: str, *, error: str | None = None, selected_id: int | None = None):
    cell = _cell_or_404(campaign, coordinate)
    return {
        "campaign": campaign,
        "cell": cell,
        "facilities": avenger_service.list_facility_options(campaign, for_coordinate=cell.coordinate),
        "room_options": avenger_service.list_room_options(campaign),
        "assignable_states": [(s, BUILD_STATE_LABELS[s]) for s in ASSIGNABLE_BUILD_STATES],
        "error": error,
        "selected_id": selected_id,
    }


def _config_data(grid) -> dict:
    """JSON-serialisable draft-editor data for the configure workflow. Room
    states come from the effective catalog -- nothing is hard-coded here."""

    def image_for(asset_id):
        return url_for("catalog.serve_asset", asset_id=asset_id) if asset_id else None

    rooms = {
        str(option.object_id): {
            "id": option.object_id,
            "name": option.name,
            "effect": option.gameplay_effect,
            "reward": option.clear_reward,
            "blocks": option.blocks_construction,
            "glyph": option.glyph,
            "image": image_for(option.primary_asset_id),
        }
        for option in grid.room_options
    }
    return {
        "coordinates": [cell.coordinate for cell in grid.cells],
        "initial": {cell.coordinate: (cell.room.object_id if cell.room else None) for cell in grid.cells},
        "rooms": rooms,
        "order": [option.object_id for option in grid.room_options],
        "defaultEmptyId": next(
            (option.object_id for option in grid.room_options if option.is_default_empty), None
        ),
        "columns": grid.columns,
    }


def _render_page(campaign, *, mode: str = "view", open_cell: str | None = None, error: str | None = None,
                 selected_id: int | None = None, status: int = 200):
    grid = avenger_service.get_grid_view(campaign)
    context = {
        "campaign": campaign,
        "grid": grid,
        "mode": mode,
        "modal": None,
    }
    if mode == "configure":
        context["config_data"] = _config_data(grid)
    if open_cell:
        context["modal"] = _modal_context(campaign, open_cell, error=error, selected_id=selected_id)
    elif error:
        context["page_error"] = error
    return render_template("avenger/index.html", **context), status


@avenger_bp.route("/")
def index(campaign_id: int):
    campaign = _load_campaign(campaign_id)
    mode = "configure" if request.args.get("mode") == "configure" else "view"
    open_cell = request.args.get("cell") if mode == "view" else None
    if open_cell:
        try:
            avenger_service.get_cell_view(campaign, open_cell)
        except AvengerGridError:
            open_cell = None
    return _render_page(campaign, mode=mode, open_cell=open_cell)


@avenger_bp.route("/configure", methods=["POST"])
def save_layout(campaign_id: int):
    """Save Layout: apply the draft atomically. Form fields are `room_<coord>`."""
    campaign = _load_campaign(campaign_id)
    draft: dict[str, int] = {}
    try:
        for key, value in request.form.items():
            if not key.startswith("room_") or not value:
                continue
            draft[key[len("room_"):]] = int(value)
        avenger_service.configure_layout(campaign, draft)
    except (ValueError, AvengerGridError) as exc:
        return _render_page(campaign, mode="configure", error=str(exc), status=400)
    return _done(campaign_id)


@avenger_bp.route("/reset", methods=["POST"])
def reset_layout(campaign_id: int):
    campaign = _load_campaign(campaign_id)
    avenger_service.reset_layout(campaign, clear_facilities=request.form.get("clear_facilities") == "1")
    return _done(campaign_id)


@avenger_bp.route("/cells/<coordinate>")
def cell_modal(campaign_id: int, coordinate: str):
    campaign = _load_campaign(campaign_id)
    _cell_or_404(campaign, coordinate)
    if not _is_htmx():
        return redirect(_page_url(campaign_id, cell=coordinate.upper()))
    return render_template("avenger/_cell_modal.html", **_modal_context(campaign, coordinate))


@avenger_bp.route("/cells/<coordinate>/facility", methods=["POST"])
def assign_facility(campaign_id: int, coordinate: str):
    campaign = _load_campaign(campaign_id)
    cell = _cell_or_404(campaign, coordinate)
    facility_id = request.form.get("facility_id", type=int)
    build_state = request.form.get("build_state", "planned")
    try:
        if facility_id is None:
            raise AvengerGridError("Choose a facility first.")
        avenger_service.assign_facility(campaign, cell.coordinate, facility_id, build_state=build_state)
    except AvengerGridError as exc:
        return _modal_error(campaign, cell.coordinate, str(exc), selected_id=facility_id)
    return _done(campaign_id)


@avenger_bp.route("/cells/<coordinate>/state", methods=["POST"])
def set_build_state(campaign_id: int, coordinate: str):
    campaign = _load_campaign(campaign_id)
    cell = _cell_or_404(campaign, coordinate)
    try:
        avenger_service.set_build_state(campaign, cell.coordinate, request.form.get("build_state", ""))
    except AvengerGridError as exc:
        return _modal_error(campaign, cell.coordinate, str(exc))
    return _done(campaign_id)


@avenger_bp.route("/cells/<coordinate>/clear", methods=["POST"])
def clear_facility(campaign_id: int, coordinate: str):
    campaign = _load_campaign(campaign_id)
    cell = _cell_or_404(campaign, coordinate)
    avenger_service.clear_facility(campaign, cell.coordinate)
    return _done(campaign_id)


@avenger_bp.route("/cells/<coordinate>/room", methods=["POST"])
def set_room_state(campaign_id: int, coordinate: str):
    campaign = _load_campaign(campaign_id)
    cell = _cell_or_404(campaign, coordinate)
    room_id = request.form.get("room_state_id", type=int)
    try:
        avenger_service.set_room_state(campaign, cell.coordinate, room_id)
    except AvengerGridError as exc:
        return _modal_error(campaign, cell.coordinate, str(exc))
    return _done(campaign_id, cell=cell.coordinate)


@avenger_bp.route("/facilities/<int:object_id>")
def facility_detail(campaign_id: int, object_id: int):
    campaign = _load_campaign(campaign_id)
    coordinate = request.args.get("cell")
    try:
        detail = avenger_service.get_facility_detail(campaign, object_id, for_coordinate=coordinate)
    except AvengerGridError:
        abort(404)
    return render_template("avenger/_facility_detail.html", campaign=campaign, detail=detail)


@avenger_bp.route("/prerequisites/<int:object_id>", methods=["POST"])
def set_prerequisite(campaign_id: int, object_id: int):
    campaign = _load_campaign(campaign_id)
    cell = request.form.get("cell") or None
    try:
        avenger_service.set_prerequisite_state(
            campaign, object_id, completed=request.form.get("completed") == "1"
        )
    except AvengerGridError:
        abort(404)
    return _done(campaign_id, cell=cell)


def _modal_error(campaign, coordinate: str, message: str, *, selected_id: int | None = None):
    if _is_htmx():
        html = render_template(
            "avenger/_cell_modal.html",
            **_modal_context(campaign, coordinate, error=message, selected_id=selected_id),
        )
        response = make_response(html, 200)
        response.headers["HX-Retarget"] = "[data-modal-root]"
        response.headers["HX-Reswap"] = "outerHTML"
        return response
    return _render_page(campaign, open_cell=coordinate, error=message, selected_id=selected_id, status=400)
