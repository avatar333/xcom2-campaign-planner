"""Catalog relationship repository (FR-CAT-3, ADR-0004).

Relationships are directed, typed edges with provenance. Validation of
self-relations/duplicates/cycle-policy lives in `services.catalog_relationships`
(domain/business rules); this module is plain CRUD + the lookups that
service needs.
"""
from __future__ import annotations

from sqlalchemy import or_

from app.extensions import db
from app.models import CatalogRelationship, CatalogRelationshipType


def list_relationship_types() -> list[CatalogRelationshipType]:
    return CatalogRelationshipType.query.order_by(CatalogRelationshipType.slug.asc()).all()


def get_relationship_type(type_id: int) -> CatalogRelationshipType | None:
    return db.session.get(CatalogRelationshipType, type_id)


def get_relationship_type_by_slug(slug: str) -> CatalogRelationshipType | None:
    return CatalogRelationshipType.query.filter_by(slug=slug).first()


def get_relationship(relationship_id: int) -> CatalogRelationship | None:
    return db.session.get(CatalogRelationship, relationship_id)


def list_for_object(object_id: int) -> tuple[list[CatalogRelationship], list[CatalogRelationship]]:
    """Return (outbound, inbound) relationships for an object (FR-CAT-5)."""
    outbound = (
        CatalogRelationship.query.filter_by(source_object_id=object_id)
        .order_by(CatalogRelationship.sort_order.asc())
        .all()
    )
    inbound = (
        CatalogRelationship.query.filter_by(target_object_id=object_id)
        .order_by(CatalogRelationship.sort_order.asc())
        .all()
    )
    return outbound, inbound


def find_duplicate(
    *,
    relationship_type_id: int,
    source_object_id: int,
    target_object_id: int,
    group_key: str | None,
) -> CatalogRelationship | None:
    return CatalogRelationship.query.filter_by(
        relationship_type_id=relationship_type_id,
        source_object_id=source_object_id,
        target_object_id=target_object_id,
        group_key=group_key,
    ).first()


def exists_edge(source_object_id: int, target_object_id: int) -> bool:
    """Whether *any* relationship already connects these two objects
    (either direction) -- used by the duplicate-relation UI guard."""
    return (
        CatalogRelationship.query.filter(
            or_(
                (CatalogRelationship.source_object_id == source_object_id)
                & (CatalogRelationship.target_object_id == target_object_id),
                (CatalogRelationship.source_object_id == target_object_id)
                & (CatalogRelationship.target_object_id == source_object_id),
            )
        ).first()
        is not None
    )


def create_relationship(**fields) -> CatalogRelationship:
    relationship = CatalogRelationship(**fields)
    db.session.add(relationship)
    db.session.commit()
    return relationship


def delete_relationship(relationship: CatalogRelationship) -> None:
    db.session.delete(relationship)
    db.session.commit()
