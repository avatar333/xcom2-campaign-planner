"""Catalog browse/CRUD/relationship routes (FR-CAT-1..8, FR-AST-*, ARCHITECTURE.md §3).

Thin routes: parse request -> call service -> render (ARCHITECTURE.md §2).
Campaign-source filtering (FR-CMP-2/3): when a campaign is active, the
catalog grid only shows objects from that campaign's *enabled* sources
(via the effective-catalog resolver, ADR-0013); without an active campaign
the catalog shows every source (global admin browse).
"""
from __future__ import annotations

import json

from flask import Blueprint, abort, redirect, render_template, request, url_for

from app.domain.attributes import parse_attributes_json
from app.forms import CatalogImportForm, CatalogObjectForm, CatalogRelationshipForm, ImageUploadForm
from app.importer.document import from_json, to_json
from app.importer.validator import validate_document
from app.repositories import catalog_objects as objects_repo
from app.repositories import catalog_relationships as relationships_repo
from app.repositories import content_sources as sources_repo
from app.repositories import object_types as object_types_repo
from app.services import asset_service
from app.services import catalog_import as import_service
from app.services import catalog_objects as objects_service
from app.services import catalog_relationships as relationships_service
from app.services import effective_catalog as effective_catalog_service
from app.services.asset_service import AssetValidationError
from app.services.campaign_context import get_active_campaign
from app.services.catalog_objects import CatalogObjectValidationError
from app.services.catalog_relationships import RelationshipValidationError
from app.services.content_sources import get_or_create_user_source

catalog_bp = Blueprint("catalog", __name__, url_prefix="/catalog")

PER_PAGE = 24


def _is_htmx() -> bool:
    return request.headers.get("HX-Request") == "true"


def _active_campaign_source_ids() -> list[int] | None:
    """`None` = no filtering (no active campaign); a list (possibly empty)
    = restrict to that campaign's effective/enabled sources."""
    campaign = get_active_campaign()
    if campaign is None:
        return None
    return effective_catalog_service.effective_source_ids(campaign.id)


@catalog_bp.route("/")
def index():
    campaign = get_active_campaign()
    source_ids = _active_campaign_source_ids()

    q = request.args.get("q", "").strip()
    type_id = request.args.get("type_id", type=int)
    source_id = request.args.get("source_id", type=int)
    tag = request.args.get("tag", "").strip()
    view = request.args.get("view", "grid")
    page = max(request.args.get("page", 1, type=int) or 1, 1)

    effective_source_ids = source_ids
    if source_id is not None:
        effective_source_ids = [source_id] if (source_ids is None or source_id in source_ids) else []

    objects, total = objects_repo.search(
        source_ids=effective_source_ids,
        type_id=type_id,
        text=q or None,
        tag=tag or None,
        page=page,
        per_page=PER_PAGE,
    )

    object_types = object_types_repo.list_object_types()
    all_sources = sources_repo.list_content_sources()
    available_sources = (
        [s for s in all_sources if s.id in source_ids] if source_ids is not None else all_sources
    )

    type_by_id = {t.id: t for t in object_types}
    source_by_id = {s.id: s for s in all_sources}

    total_pages = max((total + PER_PAGE - 1) // PER_PAGE, 1)

    return render_template(
        "catalog/index.html",
        objects=objects,
        total=total,
        page=page,
        total_pages=total_pages,
        object_types=object_types,
        sources=available_sources,
        all_sources=all_sources,
        type_by_id=type_by_id,
        source_by_id=source_by_id,
        campaign=campaign,
        q=q,
        type_id=type_id,
        source_id=source_id,
        tag=tag,
        view=view,
    )


def _build_object_form(*args, **kwargs) -> CatalogObjectForm:
    form = CatalogObjectForm(*args, **kwargs)
    form.source_id.choices = [(s.id, f"{s.name} ({s.kind})") for s in sources_repo.list_content_sources()]
    form.type_id.choices = [(t.id, t.name) for t in object_types_repo.list_object_types()]
    return form


@catalog_bp.route("/objects/new", methods=["GET", "POST"])
def new_object():
    form = _build_object_form()
    preselect_source = request.args.get("source_id", type=int)
    preselect_type = request.args.get("type_id", type=int)
    if request.method == "GET":
        user_source = get_or_create_user_source()
        form.source_id.data = preselect_source or user_source.id
        if preselect_type:
            form.type_id.data = preselect_type

    if form.validate_on_submit():
        try:
            attributes = parse_attributes_json(form.attributes_json.data)
            tags = [t.strip() for t in (form.tags.data or "").split(",") if t.strip()]
            tier = int(form.tier.data) if (form.tier.data or "").strip() else None
            obj = objects_service.create_object(
                source_id=form.source_id.data,
                type_id=form.type_id.data,
                name=form.name.data,
                template_key=form.template_key.data,
                short_description=form.short_description.data,
                long_description=form.long_description.data,
                tier=tier,
                tags=tags,
                attributes=attributes,
                is_user_authored=True,
            )
        except (CatalogObjectValidationError, ValueError) as exc:
            form.name.errors.append(str(exc))
        else:
            return redirect(url_for("catalog.detail", object_id=obj.id))

    return render_template("catalog/object_form.html", form=form, mode="create", obj=None)


@catalog_bp.route("/objects/<int:object_id>")
def detail(object_id: int):
    obj = objects_repo.get_object(object_id)
    if obj is None:
        abort(404)
    return _render_detail(obj)


def _tags_for(object_id: int):
    from app.models import CatalogObjectTag

    return CatalogObjectTag.query.filter_by(object_id=object_id).all()


def _assets_for(object_id: int):
    from app.repositories import assets as assets_repo

    links = assets_repo.list_for_object(object_id)
    result = []
    for link in links:
        asset = assets_repo.get_asset(link.asset_id)
        if asset is not None:
            result.append((link, asset))
    return result


def _enrich_relationships(relationships, *, name_key: str):
    """Attach display-ready fields (relationship_type, target_name/source_name)
    to plain CatalogRelationship rows for the detail template, since the ORM
    model intentionally carries no convenience relationships (ADR-0004 keeps
    the model a thin mirror of the schema)."""
    enriched = []
    for rel in relationships:
        relationship_type = relationships_repo.get_relationship_type(rel.relationship_type_id)
        other_id = rel.target_object_id if name_key == "target_name" else rel.source_object_id
        other_obj = objects_repo.get_object(other_id)
        enriched.append(
            {
                "id": rel.id,
                "relationship_type": relationship_type,
                "amount": rel.amount,
                "unit": rel.unit,
                "group_key": rel.group_key,
                "source_object_id": rel.source_object_id,
                "target_object_id": rel.target_object_id,
                name_key: other_obj.name if other_obj else "(missing object)",
            }
        )
    return enriched


@catalog_bp.route("/objects/<int:object_id>/edit", methods=["GET", "POST"])
def edit_object(object_id: int):
    obj = objects_repo.get_object(object_id)
    if obj is None:
        abort(404)

    form = _build_object_form(obj=obj)
    if request.method == "GET":
        form.source_id.data = obj.source_id
        form.type_id.data = obj.type_id
        form.tier.data = str(obj.tier) if obj.tier is not None else ""
        tags = _tags_for(object_id)
        form.tags.data = ", ".join(t.tag for t in tags)
        form.attributes_json.data = json.dumps(parse_attributes_json(obj.attributes), indent=2)

    if form.validate_on_submit():
        try:
            attributes = parse_attributes_json(form.attributes_json.data)
            tags = [t.strip() for t in (form.tags.data or "").split(",") if t.strip()]
            tier = int(form.tier.data) if (form.tier.data or "").strip() else None
            objects_service.update_object(
                obj,
                name=form.name.data,
                short_description=form.short_description.data,
                long_description=form.long_description.data,
                tier=tier,
                tags=tags,
                attributes=attributes,
            )
        except (CatalogObjectValidationError, ValueError) as exc:
            form.name.errors.append(str(exc))
        else:
            return redirect(url_for("catalog.detail", object_id=obj.id))

    return render_template("catalog/object_form.html", form=form, mode="edit", obj=obj)


@catalog_bp.route("/objects/<int:object_id>/delete", methods=["POST"])
def delete_object(object_id: int):
    obj = objects_repo.get_object(object_id)
    if obj is None:
        abort(404)
    status = objects_service.retire_or_delete(obj)
    if status == "deleted":
        return redirect(url_for("catalog.index"))
    return redirect(url_for("catalog.detail", object_id=object_id))


@catalog_bp.route("/objects/<int:object_id>/overlay", methods=["POST"])
def create_overlay(object_id: int):
    """FR-CAT-6: create a user-source overlay instead of editing a
    built-in/imported object directly."""
    base_obj = objects_repo.get_object(object_id)
    if base_obj is None:
        abort(404)
    user_source = get_or_create_user_source()
    overlay = objects_service.create_overlay(base_obj, user_source_id=user_source.id)
    return redirect(url_for("catalog.edit_object", object_id=overlay.id))


@catalog_bp.route("/objects/<int:object_id>/image", methods=["POST"])
def upload_image(object_id: int):
    obj = objects_repo.get_object(object_id)
    if obj is None:
        abort(404)

    form = ImageUploadForm()
    uploaded = request.files.get("image_file")
    error = None
    if not form.validate_on_submit():
        error = "Invalid form submission."
    elif uploaded is None or uploaded.filename == "":
        error = "Choose an image file to upload."
    else:
        try:
            raw_bytes = uploaded.read()
            asset_service.upload_image_for_object(
                object_id,
                role=form.role.data,
                raw_bytes=raw_bytes,
                source_url=form.source_url.data,
                license=form.license.data,
                attribution_text=form.attribution_text.data,
            )
        except AssetValidationError as exc:
            error = str(exc)

    if error:
        return _render_detail(obj, image_error=error), 400

    return redirect(url_for("catalog.detail", object_id=object_id))


@catalog_bp.route("/assets/<int:asset_id>")
def serve_asset(asset_id: int):
    from flask import Response

    from app.repositories import assets as assets_repo

    asset = assets_repo.get_asset(asset_id)
    if asset is None:
        abort(404)
    data = asset_service.read_asset_bytes(asset)
    response = Response(data, mimetype=asset.mime_type)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


def _render_detail(obj, **extra):
    source, object_type = objects_repo.object_with_source_and_type(obj)
    outbound, inbound = relationships_repo.list_for_object(obj.id)
    context = {
        "obj": obj,
        "source": source,
        "object_type": object_type,
        "outbound": _enrich_relationships(outbound, name_key="target_name"),
        "inbound": _enrich_relationships(inbound, name_key="source_name"),
        "tags": [t.tag for t in _tags_for(obj.id)],
        "object_assets": _assets_for(obj.id),
        "attributes": parse_attributes_json(obj.attributes),
        "attribute_defs": object_types_repo.list_attribute_definitions(obj.type_id),
        "relationship_types": relationships_repo.list_relationship_types(),
        "overlay_base": objects_repo.get_object(obj.overrides_object_id) if obj.overrides_object_id else None,
    }
    context.update(extra)
    return render_template("catalog/detail.html", **context)


@catalog_bp.route("/objects/<int:object_id>/relationships", methods=["POST"])
def add_relationship(object_id: int):
    obj = objects_repo.get_object(object_id)
    if obj is None:
        abort(404)

    form = CatalogRelationshipForm()
    form.relationship_type_id.choices = [(t.id, t.name) for t in relationships_repo.list_relationship_types()]

    error = None
    if not form.validate_on_submit():
        error = "Invalid relationship submission."
    else:
        relationship_type = relationships_repo.get_relationship_type(form.relationship_type_id.data)
        target_object = objects_repo.get_object(int(form.target_object_id.data))

        if relationship_type is None:
            error = "Select a relationship type."
        elif target_object is None:
            error = "Select a target object."
        else:
            direction = form.direction.data
            source_object = obj if direction == "outbound" else target_object
            real_target = target_object if direction == "outbound" else obj
            try:
                relationships_service.create_relationship(
                    relationship_type=relationship_type,
                    source_object=source_object,
                    target_object=real_target,
                    group_key=form.group_key.data,
                    amount=form.amount.data,
                    unit=form.unit.data,
                    source_pack_id=obj.source_id,
                )
            except RelationshipValidationError as exc:
                error = str(exc)

    if error:
        return _render_detail(obj, relationship_error=error), 400

    return redirect(url_for("catalog.detail", object_id=object_id))


@catalog_bp.route("/relationships/<int:relationship_id>/delete", methods=["POST"])
def delete_relationship(relationship_id: int):
    relationship = relationships_repo.get_relationship(relationship_id)
    if relationship is None:
        abort(404)
    object_id = relationship.source_object_id
    relationships_service.delete_relationship(relationship)
    return redirect(url_for("catalog.detail", object_id=object_id))


@catalog_bp.route("/search.json")
def search_json():
    """Lightweight JSON object picker endpoint (image + name selector,
    UI_UX_SPEC.md §4 object pickers), used by the relationship-add modal."""
    q = request.args.get("q", "").strip()
    exclude_id = request.args.get("exclude_id", type=int)
    objects, _total = objects_repo.search(text=q or None, page=1, per_page=20)
    results = []
    for obj in objects:
        if obj.id == exclude_id:
            continue
        results.append(
            {
                "id": obj.id,
                "name": obj.name,
                "image_url": url_for("catalog.serve_asset", asset_id=obj.primary_asset_id)
                if obj.primary_asset_id
                else None,
            }
        )
    return {"results": results}


@catalog_bp.route("/export")
def export_catalog():
    from flask import Response

    source_id = request.args.get("source_id", type=int)
    document = import_service.export_document(source_ids=[source_id] if source_id else None)
    payload = to_json(document)
    response = Response(payload, mimetype="application/json")
    response.headers["Content-Disposition"] = "attachment; filename=catalog-export.json"
    return response


@catalog_bp.route("/import", methods=["GET", "POST"])
def import_catalog():
    form = CatalogImportForm()
    report = None
    document = None
    error = None
    result = None

    if request.method == "POST":
        uploaded = request.files.get("document_file")
        raw_text = request.form.get("document_text")
        try:
            if uploaded and uploaded.filename:
                raw_text = uploaded.read().decode("utf-8")
            if not raw_text or not raw_text.strip():
                raise ValueError("Provide a JSON document to import (upload a file or paste it).")
            document = from_json(raw_text)
            report = validate_document(document)
        except ValueError as exc:
            error = str(exc)

        if document is not None and report is not None and report.is_valid and form.confirm.data:
            try:
                result = import_service.import_document(document)
            except import_service.CatalogImportError as exc:
                error = str(exc)

        if document is not None and raw_text:
            # Preserve the pasted/uploaded text across the confirm round-trip.
            request_text_for_template = raw_text
        else:
            request_text_for_template = raw_text or ""
    else:
        request_text_for_template = ""

    return render_template(
        "catalog/import.html",
        form=form,
        report=report,
        error=error,
        result=result,
        document_text=request_text_for_template,
    )
