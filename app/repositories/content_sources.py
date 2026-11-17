"""Content source repository (DOMAIN_MODEL.md §3, FR-SRC-1..6).

Content sources are global/catalog-scoped (never reference campaigns,
ADR-0002). CRUD here is plain, since uniqueness/identity rules are simple
enough not to need a separate service layer beyond `services/content_sources.py`
for slug generation and relation management.
"""
from __future__ import annotations

from app.extensions import db
from app.models import ContentSource, ContentSourceRelation


def list_content_sources(*, kind: str | None = None) -> list[ContentSource]:
    query = ContentSource.query
    if kind:
        query = query.filter(ContentSource.kind == kind)
    return query.order_by(ContentSource.is_builtin.desc(), ContentSource.name.asc()).all()


def get_content_source(source_id: int) -> ContentSource | None:
    return db.session.get(ContentSource, source_id)


def get_content_source_by_slug(slug: str) -> ContentSource | None:
    return ContentSource.query.filter_by(slug=slug).first()


def create_content_source(**fields) -> ContentSource:
    source = ContentSource(**fields)
    db.session.add(source)
    db.session.commit()
    return source


def update_content_source(source: ContentSource, **fields) -> ContentSource:
    for key, value in fields.items():
        setattr(source, key, value)
    db.session.commit()
    return source


def delete_content_source(source: ContentSource) -> None:
    """Hard delete -- only safe for a source with no catalog objects yet.

    Callers (`services.content_sources`) must check `has_catalog_objects`
    first; a source already in use must be retired/disabled instead
    (DATABASE_SCHEMA.md §1.2 delete-behaviour note).
    """
    db.session.delete(source)
    db.session.commit()


def has_catalog_objects(source: ContentSource) -> bool:
    from app.models import CatalogObject

    return (
        db.session.query(CatalogObject.id).filter_by(source_id=source.id).first()
        is not None
    )


def is_used_by_any_campaign(source: ContentSource) -> bool:
    from app.models import CampaignContentSource

    return (
        db.session.query(CampaignContentSource.id)
        .filter_by(content_source_id=source.id)
        .first()
        is not None
    )


def list_relations(source: ContentSource) -> list[ContentSourceRelation]:
    return ContentSourceRelation.query.filter_by(source_id=source.id).all()


def set_relations(source: ContentSource, relations: list[tuple[str, int]]) -> None:
    """Replace a source's declared relations with the given (relation, related_source_id) pairs."""
    ContentSourceRelation.query.filter_by(source_id=source.id).delete()
    for relation, related_source_id in relations:
        db.session.add(
            ContentSourceRelation(
                source_id=source.id,
                related_source_id=related_source_id,
                relation=relation,
            )
        )
    db.session.commit()
