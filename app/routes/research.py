"""Research & Dependency Planner routes (ADR-0021).

Thin routes: load the campaign (404 if absent), call `app.services.research`,
render. Every URL carries the campaign id, so switching campaigns is just
navigating to another campaign's URL.
"""
from __future__ import annotations

from urllib.parse import quote, urlparse

from flask import (
    Blueprint,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

from app.repositories import campaigns as campaigns_repo
from app.services import research as research_service
from app.services.campaign_context import set_active_campaign
from app.services.research import ResearchError, ResearchFilters

research_bp = Blueprint("research", __name__, url_prefix="/campaigns/<int:campaign_id>/research")

STATUS_COLOURS = {
    "completed": "#59c97a",
    "in_progress": "#e8b64c",
    "planned": "#8fb7ff",
    "available": "#35d1e6",
    "blocked": "#ef5b5b",
}


def _load_campaign(campaign_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)
    set_active_campaign(campaign.id)
    return campaign


def _safe_next(campaign_id: int, fallback: str) -> str:
    """Only ever redirect back into this campaign's own research pages."""
    candidate = request.form.get("next") or request.args.get("next") or ""
    parsed = urlparse(candidate)
    prefix = f"/campaigns/{campaign_id}/research"
    if parsed.scheme or parsed.netloc or not parsed.path.startswith(prefix):
        return fallback
    return candidate


def _with_error(url: str, message: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}error={quote(message[:200])}"


def _is_json() -> bool:
    return request.headers.get("X-Requested-With") == "fetch" or request.accept_mimetypes.best == "application/json"


def _respond(campaign_id: int, fallback: str, error: str | None = None):
    if _is_json():
        if error:
            return jsonify({"ok": False, "error": error}), 400
        return jsonify({"ok": True})
    target = _safe_next(campaign_id, fallback)
    return redirect(_with_error(target, error) if error else target)


def _glyph_uri(glyph: str, status: str) -> str:
    colour = STATUS_COLOURS.get(status, "#9fb4c4")
    svg = glyph.replace("currentColor", colour)
    if "xmlns" not in svg:
        svg = svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    return "data:image/svg+xml;utf8," + quote(svg)


# ------------------------------------------------------------------ pages


@research_bp.route("/")
def index(campaign_id: int):
    campaign = _load_campaign(campaign_id)
    filters = ResearchFilters(
        q=request.args.get("q", "").strip(),
        category=request.args.get("category", "").strip(),
        status=request.args.get("status", "").strip(),
        source_id=request.args.get("source_id", type=int),
        type_slug=request.args.get("type", "").strip(),
    )
    browse = research_service.browse_research(campaign, filters)
    queue = research_service.get_queue_view(campaign, with_suggestion=request.args.get("suggest") == "1")
    return render_template(
        "research/index.html",
        campaign=campaign,
        browse=browse,
        queue=queue,
        filters=filters,
        error=request.args.get("error"),
    )


@research_bp.route("/<int:object_id>")
def detail(campaign_id: int, object_id: int):
    campaign = _load_campaign(campaign_id)
    try:
        detail_view = research_service.get_research_detail(campaign, object_id)
    except ResearchError:
        abort(404)
    return render_template(
        "research/detail.html", campaign=campaign, detail=detail_view, error=request.args.get("error")
    )


@research_bp.route("/path/")
def path_picker(campaign_id: int):
    campaign = _load_campaign(campaign_id)
    q = request.args.get("q", "").strip()
    type_slug = request.args.get("type", "").strip()
    return render_template(
        "research/path_picker.html",
        campaign=campaign,
        q=q,
        type_slug=type_slug,
        targets=research_service.list_path_targets(campaign, q=q, type_slug=type_slug),
        types=research_service.path_target_types(campaign),
    )


@research_bp.route("/path/<int:object_id>")
def unlock_path(campaign_id: int, object_id: int):
    campaign = _load_campaign(campaign_id)
    try:
        path = research_service.get_unlock_path(campaign, object_id)
    except ResearchError:
        abort(404)
    payload = research_service.build_graph_payload(
        path,
        image_url=lambda asset_id: url_for("catalog.serve_asset", asset_id=asset_id),
        glyph_uri=_glyph_uri,
        detail_url=lambda oid: url_for("research.detail", campaign_id=campaign.id, object_id=oid),
    )
    return render_template(
        "research/path.html",
        campaign=campaign,
        path=path,
        graph={"nodes": payload.nodes, "edges": payload.edges},
        error=request.args.get("error"),
    )


# ---------------------------------------------------------------- actions


def _queue_fallback(campaign_id: int) -> str:
    return url_for("research.index", campaign_id=campaign_id)


def _run(campaign_id: int, action, *args, **kwargs):
    campaign = _load_campaign(campaign_id)
    try:
        action(campaign, *args, **kwargs)
    except (ResearchError, ValueError) as exc:
        return _respond(campaign_id, _queue_fallback(campaign_id), error=str(exc))
    return _respond(campaign_id, _queue_fallback(campaign_id))


@research_bp.route("/<int:object_id>/queue", methods=["POST"])
def queue_add(campaign_id: int, object_id: int):
    return _run(campaign_id, research_service.add_to_queue, object_id)


@research_bp.route("/<int:object_id>/unqueue", methods=["POST"])
def queue_remove(campaign_id: int, object_id: int):
    return _run(campaign_id, research_service.remove_from_queue, object_id)


@research_bp.route("/<int:object_id>/start", methods=["POST"])
def start(campaign_id: int, object_id: int):
    return _run(campaign_id, research_service.start_research, object_id)


@research_bp.route("/<int:object_id>/complete", methods=["POST"])
def complete(campaign_id: int, object_id: int):
    return _run(campaign_id, research_service.complete_research, object_id)


@research_bp.route("/<int:object_id>/reopen", methods=["POST"])
def reopen(campaign_id: int, object_id: int):
    return _run(campaign_id, research_service.reopen_research, object_id)


@research_bp.route("/<int:object_id>/move", methods=["POST"])
def move(campaign_id: int, object_id: int):
    direction = request.form.get("direction", "")
    offsets = {"up": -1, "down": 1}
    if direction in offsets:
        return _run(campaign_id, research_service.move_in_queue, object_id, offset=offsets[direction])
    if direction == "top":
        return _run(campaign_id, research_service.move_in_queue, object_id, to_position=1)
    if direction == "bottom":
        return _run(campaign_id, research_service.move_in_queue, object_id, to_position=10**6)
    position = request.form.get("position", type=int)
    if position is not None:
        return _run(campaign_id, research_service.move_in_queue, object_id, to_position=position)
    return _respond(campaign_id, _queue_fallback(campaign_id), error="Say where to move the project.")


def _order_from_form() -> list[int]:
    raw = request.form.getlist("order") or [p for p in request.form.get("order_csv", "").split(",") if p]
    return [int(v) for v in raw]


@research_bp.route("/queue/reorder", methods=["POST"])
def reorder(campaign_id: int):
    campaign = _load_campaign(campaign_id)
    try:
        research_service.reorder_queue(campaign, _order_from_form())
    except (ResearchError, ValueError) as exc:
        return _respond(campaign_id, _queue_fallback(campaign_id), error=str(exc))
    return _respond(campaign_id, _queue_fallback(campaign_id))


@research_bp.route("/queue/apply-suggestion", methods=["POST"])
def apply_suggestion(campaign_id: int):
    """Applies only the exact order the user was shown and confirmed -- never a
    server-side recomputation they have not seen."""
    campaign = _load_campaign(campaign_id)
    try:
        research_service.apply_queue_order(campaign, _order_from_form())
    except (ResearchError, ValueError) as exc:
        return _respond(campaign_id, _queue_fallback(campaign_id), error=str(exc))
    return _respond(campaign_id, _queue_fallback(campaign_id))


@research_bp.route("/graph/<int:object_id>.json")
def graph_json(campaign_id: int, object_id: int):
    campaign = _load_campaign(campaign_id)
    try:
        path = research_service.get_unlock_path(campaign, object_id)
    except ResearchError:
        abort(404)
    payload = research_service.build_graph_payload(
        path,
        image_url=lambda asset_id: url_for("catalog.serve_asset", asset_id=asset_id),
        glyph_uri=_glyph_uri,
        detail_url=lambda oid: url_for("research.detail", campaign_id=campaign.id, object_id=oid),
    )
    response = make_response(jsonify({"nodes": payload.nodes, "edges": payload.edges}))
    response.headers["Cache-Control"] = "no-store"
    return response



