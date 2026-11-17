"""Content-source admin routes (FR-SRC-1/3/6, not campaign-scoped -- global
catalog admin, reachable from Catalog's "Manage sources" action and future
Settings screen per UI_UX_SPEC.md §2)."""
from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, url_for

from app.forms import ContentSourceForm
from app.repositories import content_sources as repo
from app.services import content_sources as service
from app.services.content_sources import ContentSourceInUseError, ContentSourceValidationError

content_sources_bp = Blueprint("content_sources", __name__, url_prefix="/content/sources")


def _is_htmx() -> bool:
    return request.headers.get("HX-Request") == "true"


@content_sources_bp.route("/")
def list_sources():
    sources = repo.list_content_sources()
    return render_template("content_sources/list.html", sources=sources)


@content_sources_bp.route("/new", methods=["GET", "POST"])
def new_source():
    form = ContentSourceForm()
    if form.validate_on_submit():
        try:
            source = service.create_content_source(
                name=form.name.data,
                kind=form.kind.data,
                version=form.version.data,
                steam_workshop_id=form.steam_workshop_id.data,
                description=form.description.data,
                license=form.license.data,
                attribution_text=form.attribution_text.data,
                homepage_url=form.homepage_url.data,
            )
        except ContentSourceValidationError as exc:
            form.name.errors.append(str(exc))
        else:
            if _is_htmx():
                response = redirect(url_for("content_sources.list_sources"))
                response.headers["HX-Redirect"] = url_for("content_sources.list_sources")
                return response
            return redirect(url_for("content_sources.detail", source_id=source.id))

    template = "content_sources/_form_modal.html" if _is_htmx() else "content_sources/new.html"
    return render_template(template, form=form, mode="create", source=None)


@content_sources_bp.route("/<int:source_id>")
def detail(source_id: int):
    source = repo.get_content_source(source_id)
    if source is None:
        abort(404)
    return render_template("content_sources/detail.html", source=source)


@content_sources_bp.route("/<int:source_id>/edit", methods=["GET", "POST"])
def edit_source(source_id: int):
    source = repo.get_content_source(source_id)
    if source is None:
        abort(404)

    form = ContentSourceForm(obj=source)
    if form.validate_on_submit():
        try:
            service.update_content_source(
                source,
                name=form.name.data,
                kind=form.kind.data,
                version=form.version.data,
                steam_workshop_id=form.steam_workshop_id.data,
                description=form.description.data,
                license=form.license.data,
                attribution_text=form.attribution_text.data,
                homepage_url=form.homepage_url.data,
            )
        except ContentSourceValidationError as exc:
            form.name.errors.append(str(exc))
        else:
            if _is_htmx():
                response = redirect(url_for("content_sources.detail", source_id=source.id))
                response.headers["HX-Redirect"] = url_for("content_sources.detail", source_id=source.id)
                return response
            return redirect(url_for("content_sources.detail", source_id=source.id))

    template = "content_sources/_form_modal.html" if _is_htmx() else "content_sources/edit.html"
    return render_template(template, form=form, mode="edit", source=source)


@content_sources_bp.route("/<int:source_id>/delete", methods=["POST"])
def delete_source(source_id: int):
    source = repo.get_content_source(source_id)
    if source is None:
        abort(404)
    try:
        service.delete_content_source(source)
    except ContentSourceInUseError as exc:
        return render_template(
            "content_sources/detail.html", source=source, delete_error=str(exc)
        ), 400
    return redirect(url_for("content_sources.list_sources"))
