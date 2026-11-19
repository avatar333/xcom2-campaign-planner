"""Soldier roster, Build Library, and build editor routes (ADR-0022).

Thin routes: load the campaign (404 if absent), call `app.services.soldiers`
/ `app.services.builds`, render. Every URL carries the campaign id.
"""
from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, url_for

from app.models.catalog import SOLDIER_STAT_KEYS
from app.repositories import campaigns as campaigns_repo
from app.services import builds as builds_service
from app.services import soldiers as soldiers_service
from app.services.builds import BuildContext, BuildError
from app.services.campaign_context import set_active_campaign
from app.services.soldiers import SoldierError

soldiers_bp = Blueprint("soldiers", __name__, url_prefix="/campaigns/<int:campaign_id>/soldiers")
builds_bp = Blueprint("builds", __name__, url_prefix="/campaigns/<int:campaign_id>/builds")


def _load_campaign(campaign_id: int):
    campaign = campaigns_repo.get_campaign(campaign_id)
    if campaign is None:
        abort(404)
    set_active_campaign(campaign.id)
    return campaign


def _form_value(name: str) -> str | None:
    value = request.form.get(name, "").strip()
    return value or None


# ----------------------------------------------------------------- roster


@soldiers_bp.route("/")
def index(campaign_id: int):
    campaign = _load_campaign(campaign_id)
    return render_template(
        "soldiers/index.html", campaign=campaign, soldiers=soldiers_service.list_soldier_views(campaign)
    )


@soldiers_bp.route("/new", methods=["GET", "POST"])
def new_soldier(campaign_id: int):
    campaign = _load_campaign(campaign_id)
    ctx = BuildContext(campaign)
    if request.method == "POST":
        try:
            portrait = request.files.get("portrait_file")
            soldier = soldiers_service.create_soldier(
                campaign,
                name=request.form.get("name", ""),
                surname=_form_value("surname"),
                callsign=_form_value("callsign"),
                class_object_id=request.form.get("class_object_id", type=int),
                rank_object_id=request.form.get("rank_object_id", type=int),
                notes=_form_value("notes"),
                status=request.form.get("status", "active"),
                portrait_bytes=(portrait.read() if portrait and portrait.filename else None),
            )
        except SoldierError as exc:
            return _render_soldier_form(campaign, ctx, error=str(exc), form=request.form), 400
        return redirect(url_for("soldiers.soldier_detail", campaign_id=campaign.id, soldier_id=soldier.id))
    return _render_soldier_form(campaign, ctx)


def _selected_id(form, field: str, saved: int | None) -> int | None:
    """The id to show as selected: what the user just submitted (re-render after
    a validation error), else the saved value. Always an int or None -- raw
    request text never reaches a template."""
    if form and field in form:
        try:
            return int(form.get(field, ""))
        except (TypeError, ValueError):
            return None
    return saved

def _render_soldier_form(campaign, ctx, *, soldier=None, error=None, form=None):
    form = form or {}
    return render_template(
        "soldiers/form.html",
        campaign=campaign,
        soldier=soldier,
        classes=soldiers_service.list_class_options(campaign, ctx=ctx),
        ranks=soldiers_service.list_rank_options(campaign, ctx=ctx),
        error=error,
        form=form,
        selected_class_id=_selected_id(form, "class_object_id", soldier.class_object_id if soldier else None),
        selected_rank_id=_selected_id(form, "rank_object_id", soldier.rank_object_id if soldier else None),
    )

@soldiers_bp.route("/<int:soldier_id>")
def soldier_detail(campaign_id: int, soldier_id: int):
    campaign = _load_campaign(campaign_id)
    soldier = soldiers_service.get_soldier(campaign, soldier_id)
    if soldier is None:
        abort(404)
    view = soldiers_service.soldier_view(campaign, soldier)
    return render_template(
        "soldiers/detail.html",
        campaign=campaign,
        soldier=soldier,
        view=view,
        stat_keys=SOLDIER_STAT_KEYS,
        build_types=builds_service.BUILD_TYPES,
        builds_by_type={bt: builds_service.list_builds(campaign, bt) for bt in builds_service.BUILD_TYPES},
        error=request.args.get("error"),
    )


@soldiers_bp.route("/<int:soldier_id>/edit", methods=["GET", "POST"])
def edit_soldier(campaign_id: int, soldier_id: int):
    campaign = _load_campaign(campaign_id)
    soldier = soldiers_service.get_soldier(campaign, soldier_id)
    if soldier is None:
        abort(404)
    ctx = BuildContext(campaign)
    if request.method == "POST":
        try:
            portrait = request.files.get("portrait_file")
            soldiers_service.update_soldier(
                campaign, soldier,
                name=request.form.get("name", ""), surname=_form_value("surname"), callsign=_form_value("callsign"),
                class_object_id=request.form.get("class_object_id", type=int),
                rank_object_id=request.form.get("rank_object_id", type=int),
                notes=_form_value("notes"), status=request.form.get("status", soldier.status),
                portrait_bytes=(portrait.read() if portrait and portrait.filename else None),
            )
        except SoldierError as exc:
            return _render_soldier_form(campaign, ctx, soldier=soldier, error=str(exc), form=request.form), 400
        return redirect(url_for("soldiers.soldier_detail", campaign_id=campaign.id, soldier_id=soldier.id))
    return _render_soldier_form(campaign, ctx, soldier=soldier)


@soldiers_bp.route("/<int:soldier_id>/delete", methods=["POST"])
def delete_soldier(campaign_id: int, soldier_id: int):
    campaign = _load_campaign(campaign_id)
    soldier = soldiers_service.get_soldier(campaign, soldier_id)
    if soldier is None:
        abort(404)
    soldiers_service.delete_soldier(campaign, soldier)
    return redirect(url_for("soldiers.index", campaign_id=campaign.id))


@soldiers_bp.route("/<int:soldier_id>/stats", methods=["POST"])
def update_stats(campaign_id: int, soldier_id: int):
    campaign = _load_campaign(campaign_id)
    soldier = soldiers_service.get_soldier(campaign, soldier_id)
    if soldier is None:
        abort(404)
    try:
        soldiers_service.set_stats(campaign, soldier, {k: request.form.get(k) for k in SOLDIER_STAT_KEYS})
    except SoldierError as exc:
        return redirect(url_for("soldiers.soldier_detail", campaign_id=campaign.id, soldier_id=soldier.id, error=str(exc)))
    return redirect(url_for("soldiers.soldier_detail", campaign_id=campaign.id, soldier_id=soldier.id))


@soldiers_bp.route("/<int:soldier_id>/builds/<build_type>/<slot>", methods=["POST"])
def assign_build(campaign_id: int, soldier_id: int, build_type: str, slot: str):
    campaign = _load_campaign(campaign_id)
    soldier = soldiers_service.get_soldier(campaign, soldier_id)
    if soldier is None:
        abort(404)
    build_id = request.form.get("build_id", type=int)
    try:
        if not build_id:
            soldiers_service.unassign_build(campaign, soldier, build_type, slot)
        else:
            build = builds_service.get_build(campaign, build_id)
            if build is None:
                raise SoldierError("That build does not exist in this campaign.")
            soldiers_service.assign_build(campaign, soldier, build, assignment_slot=slot)
    except (SoldierError, BuildError) as exc:
        return redirect(url_for("soldiers.soldier_detail", campaign_id=campaign.id, soldier_id=soldier.id, error=str(exc)))
    return redirect(url_for("soldiers.soldier_detail", campaign_id=campaign.id, soldier_id=soldier.id))


# ------------------------------------------------------------- build library


@builds_bp.route("/")
def index(campaign_id: int):
    campaign = _load_campaign(campaign_id)
    build_type = request.args.get("type", "")
    return render_template(
        "builds/index.html",
        campaign=campaign,
        build_types=builds_service.BUILD_TYPES,
        selected_type=build_type,
        views=builds_service.list_build_views(campaign, build_type or None),
    )


def _editor_state(base_id, attachments) -> dict:
    """The single, JSON-serialisable initial state of the build editor's Alpine
    component -- the same shape for Create and Edit (and for a re-render after a
    validation error). Sorted so the output is deterministic."""
    return {
        "step": 3 if base_id else 1,
        "baseId": base_id,
        "attachments": [
            {"slotType": slot_type, "index": index, "objectId": object_id}
            for (slot_type, index), object_id in sorted((attachments or {}).items())
        ],
    }


def _rebase(campaign, build_type, ctx, new_base_id, attachments):
    """Move the editor onto another base object: keep the components that still
    fit, drop (and tell the user about) the rest. Returns
    `(attachments, overrides, notice)`; raises BuildError for an invalid base."""
    fitted = builds_service.fit_attachments_to_base(campaign, build_type, new_base_id, attachments, ctx=ctx)
    notice = None
    if fitted.dropped:
        notice = (
            "Base changed. These components have no matching slot on the new base and were removed from "
            "this draft: " + ", ".join(fitted.dropped) + ". Nothing is saved until you press Save Build."
        )
    return fitted.attachments, fitted.overrides, notice


def _editor_context(campaign, build_type, ctx, *, base_id=None, attachments=None, overrides=None, notice=None):
    specs, options = ([], {})
    if base_id:
        specs, options = builds_service.list_component_options(campaign, build_type, base_id, ctx=ctx)
    return {
        "campaign": campaign,
        "build_type": build_type,
        "type_spec": builds_service.get_build_type(build_type),
        "bases": builds_service.list_base_options(campaign, build_type, ctx=ctx),
        "selected_base_id": base_id,
        "slots": specs,
        "options": options,
        "attachments": attachments or {},
        "overrides": overrides or set(),
        "editor_state": _editor_state(base_id, attachments),
        "notice": notice,
    }


@builds_bp.route("/<build_type>/new", methods=["GET", "POST"])
def new_build(campaign_id: int, build_type: str):
    campaign = _load_campaign(campaign_id)
    try:
        builds_service.get_build_type(build_type)
    except BuildError:
        abort(404)
    ctx = BuildContext(campaign)
    base_id = request.values.get("base_object_id", type=int)
    attachments, overrides = _parse_build_form(request.form) if request.method == "POST" else ({}, set())
    notice = None

    if request.method == "POST" and request.form.get("rebase_to", type=int) is not None:
        try:
            attachments, overrides, notice = _rebase(campaign, build_type, ctx, request.form.get("rebase_to", type=int), attachments)
            base_id = request.form.get("rebase_to", type=int)
        except BuildError as exc:
            context = _editor_context(campaign, build_type, ctx, base_id=base_id, attachments=attachments, overrides=overrides)
            return render_template("builds/editor.html", error=str(exc), build=None, **context), 400

    elif request.method == "POST" and request.form.get("step") == "save":
        try:
            build = builds_service.create_build(
                campaign, build_type, base_object_id=base_id, name=request.form.get("name", ""),
                purpose=_form_value("purpose"), notes=_form_value("notes"), attachments=attachments, overrides=overrides,
            )
        except BuildError as exc:
            context = _editor_context(campaign, build_type, ctx, base_id=base_id, attachments=attachments, overrides=overrides)
            return render_template("builds/editor.html", error=str(exc), build=None, **context), 400
        return redirect(url_for("builds.build_detail", campaign_id=campaign.id, build_id=build.id))

    context = _editor_context(campaign, build_type, ctx, base_id=base_id, attachments=attachments, overrides=overrides, notice=notice)
    return render_template("builds/editor.html", build=None, error=request.args.get("error"), **context)


@builds_bp.route("/<int:build_id>")
def build_detail(campaign_id: int, build_id: int):
    campaign = _load_campaign(campaign_id)
    build = builds_service.get_build(campaign, build_id)
    if build is None:
        abort(404)
    return render_template("builds/detail.html", campaign=campaign, build=build, view=builds_service.build_view(campaign, build))


def _parse_build_form(form) -> tuple[dict, set]:
    attachments: dict[tuple[str, int], int] = {}
    overrides: set[tuple[str, int]] = set()
    for key, value in form.items():
        if not key.startswith("slot__") or not value:
            continue
        _prefix, slot_type, index = key.split("__", 2)
        slot_key = (slot_type, int(index))
        attachments[slot_key] = int(value)
        if form.get(f"override__{slot_type}__{index}") == "1":
            overrides.add(slot_key)
    return attachments, overrides


@builds_bp.route("/<int:build_id>/edit", methods=["GET", "POST"])
def edit_build(campaign_id: int, build_id: int):
    campaign = _load_campaign(campaign_id)
    build = builds_service.get_build(campaign, build_id)
    if build is None:
        abort(404)
    ctx = BuildContext(campaign)
    if request.method == "POST":
        base_id = request.form.get("base_object_id", type=int)
        attachments, overrides = _parse_build_form(request.form)
        if request.form.get("rebase_to", type=int) is not None:
            # Base chosen in step 1: re-render on the new base, keeping everything
            # the user has typed or picked so far (name/purpose/notes come back
            # from request.form, components are fitted to the new slots).
            try:
                attachments, overrides, notice = _rebase(campaign, build.build_type, ctx, request.form.get("rebase_to", type=int), attachments)
                base_id = request.form.get("rebase_to", type=int)
            except BuildError as exc:
                context = _editor_context(campaign, build.build_type, ctx, base_id=base_id, attachments=attachments, overrides=overrides)
                return render_template("builds/editor.html", error=str(exc), build=build, **context), 400
            context = _editor_context(campaign, build.build_type, ctx, base_id=base_id, attachments=attachments, overrides=overrides, notice=notice)
            return render_template("builds/editor.html", build=build, **context)
        if request.form.get("step") == "save":
            try:
                builds_service.update_build(
                    campaign, build, base_object_id=base_id, name=request.form.get("name", ""),
                    purpose=_form_value("purpose"), notes=_form_value("notes"), attachments=attachments, overrides=overrides,
                )
            except BuildError as exc:
                context = _editor_context(campaign, build.build_type, ctx, base_id=base_id, attachments=attachments, overrides=overrides)
                return render_template("builds/editor.html", error=str(exc), build=build, **context), 400
            return redirect(url_for("builds.build_detail", campaign_id=campaign.id, build_id=build.id))
        context = _editor_context(campaign, build.build_type, ctx, base_id=base_id, attachments=attachments, overrides=overrides)
        return render_template("builds/editor.html", build=build, **context)

    view = builds_service.build_view(campaign, build, ctx=ctx)
    attachments = {c.slot.key: c.ref.object_id for c in view.components}
    overrides = {c.slot.key for c in view.components if c.is_override}
    base_id, notice = view.base.object_id, None
    requested_base = request.args.get("base_object_id", type=int)
    if requested_base is not None and requested_base != base_id:  # deep link / bookmark to a different base
        try:
            attachments, overrides, notice = _rebase(campaign, build.build_type, ctx, requested_base, attachments)
            base_id = requested_base
        except BuildError as exc:
            notice = str(exc)
    context = _editor_context(campaign, build.build_type, ctx, base_id=base_id, attachments=attachments, overrides=overrides, notice=notice)
    return render_template("builds/editor.html", build=build, **context)


@builds_bp.route("/<int:build_id>/delete", methods=["POST"])
def delete_build(campaign_id: int, build_id: int):
    campaign = _load_campaign(campaign_id)
    build = builds_service.get_build(campaign, build_id)
    if build is None:
        abort(404)
    try:
        builds_service.delete_build(campaign, build, unassign=request.form.get("unassign") == "1")
    except BuildError as exc:
        return render_template(
            "builds/detail.html", campaign=campaign, build=build, view=builds_service.build_view(campaign, build), error=str(exc)
        ), 400
    return redirect(url_for("builds.index", campaign_id=campaign.id))


@builds_bp.route("/<build_type>/picker")
def component_picker(campaign_id: int, build_type: str):
    """htmx fragment: component/slot gallery for the chosen base."""
    campaign = _load_campaign(campaign_id)
    base_id = request.args.get("base_object_id", type=int)
    ctx = BuildContext(campaign)
    context = _editor_context(campaign, build_type, ctx, base_id=base_id)
    return render_template("builds/_slots.html", **context)
