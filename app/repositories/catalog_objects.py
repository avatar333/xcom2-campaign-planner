"""Catalog object repository (FR-CAT-1..8).

Identity note (DOMAIN_MODEL.md §5, ADR-0013): `template_key` is only unique
within `(source_id, template_key)`, never globally -- every lookup here that
resolves identity filters by both columns, never `template_key` alone.
"""
from __future__ import annotations

from sqlalchemy import or_

from app.extensions import db
from app.models import CatalogObject, CatalogObjectTag, CatalogObjectType, ContentSource


def get_object(object_id: int) -> CatalogObject | None:
    return db.session.get(CatalogObject, object_id)


def get_by_source_and_template_key(source_id: int, template_key: str) -> CatalogObject | None:
    return CatalogObject.query.filter_by(source_id=source_id, template_key=template_key).first()


def get_by_source_and_slug(source_id: int, slug: str) -> CatalogObject | None:
    return CatalogObject.query.filter_by(source_id=source_id, slug=slug).first()


def search(
    *,
    source_ids: list[int] | None = None,
    type_id: int | None = None,
    text: str | None = None,
    tag: str | None = None,
    include_retired: bool = False,
    page: int = 1,
    per_page: int = 24,
) -> tuple[list[CatalogObject], int]:
    """Filtered, paginated catalog search (FR-CAT-4).

    Returns `(page_of_objects, total_count)`. `source_ids`, when provided,
    restricts results to the campaign's effective/enabled sources (the
    campaign-source-filtering requirement) -- passing `None` means "every
    source" (global catalog browse, no campaign in scope).
    """
    query = CatalogObject.query

    if not include_retired:
        query = query.filter(CatalogObject.is_retired.is_(False))
    if source_ids is not None:
        if not source_ids:
            return [], 0
        query = query.filter(CatalogObject.source_id.in_(source_ids))
    if type_id is not None:
        query = query.filter(CatalogObject.type_id == type_id)
    if tag:
        query = query.join(CatalogObjectTag, CatalogObjectTag.object_id == CatalogObject.id).filter(
            CatalogObjectTag.tag == tag.strip().lower()
        )
    if text:
        like = f"%{text.strip()}%"
        query = query.filter(
            or_(
                CatalogObject.name.ilike(like),
                CatalogObject.short_description.ilike(like),
                CatalogObject.long_description.ilike(like),
                CatalogObject.template_key.ilike(like),
            )
        )

    total = query.count()
    items = (
        query.order_by(CatalogObject.name.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return items, total


def list_all_tags() -> list[str]:
    rows = db.session.query(CatalogObjectTag.tag).distinct().order_by(CatalogObjectTag.tag.asc())
    return [row[0] for row in rows]


def create_object(**fields) -> CatalogObject:
    obj = CatalogObject(**fields)
    db.session.add(obj)
    db.session.commit()
    return obj


def update_object(obj: CatalogObject, **fields) -> CatalogObject:
    for key, value in fields.items():
        setattr(obj, key, value)
    db.session.commit()
    return obj


def set_tags(obj: CatalogObject, tags: list[str]) -> None:
    CatalogObjectTag.query.filter_by(object_id=obj.id).delete()
    seen = set()
    for raw_tag in tags:
        tag = raw_tag.strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        db.session.add(CatalogObjectTag(object_id=obj.id, tag=tag))
    db.session.commit()


def is_referenced_by_campaign_state(obj: CatalogObject) -> bool:
    """Whether retiring (vs. hard-deleting) this object is required (FR-CAT-8)."""
    from app.models import CampaignObjectState, CampaignPlanItem, CampaignSoldierLoadout

    return (
        db.session.query(CampaignObjectState.id).filter_by(catalog_object_id=obj.id).first()
        is not None
        or db.session.query(CampaignPlanItem.id).filter_by(catalog_object_id=obj.id).first()
        is not None
        or db.session.query(CampaignSoldierLoadout.id).filter_by(item_object_id=obj.id).first()
        is not None
    )


def is_referenced_by_relationship(obj: CatalogObject) -> bool:
    from app.models import CatalogRelationship

    return (
        db.session.query(CatalogRelationship.id)
        .filter(
            or_(
                CatalogRelationship.source_object_id == obj.id,
                CatalogRelationship.target_object_id == obj.id,
            )
        )
        .first()
        is not None
    )


def retire_object(obj: CatalogObject) -> CatalogObject:
    obj.is_retired = True
    db.session.commit()
    return obj


def delete_object(obj: CatalogObject) -> None:
    db.session.delete(obj)
    db.session.commit()


def object_with_source_and_type(obj: CatalogObject) -> tuple[ContentSource, CatalogObjectType]:
    return (
        db.session.get(ContentSource, obj.source_id),
        db.session.get(CatalogObjectType, obj.type_id),
    )
