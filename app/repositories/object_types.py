"""Catalog object-type + attribute-definition repository (FR-CAT-2, ADR-0003).

Object types are data, not code; this repository is what lets the catalog
UI (and future importers) list/create types and their attribute schemas
without any Python change per new category.
"""
from __future__ import annotations

from app.extensions import db
from app.models import CatalogAttributeDefinition, CatalogObjectType


def list_object_types() -> list[CatalogObjectType]:
    return CatalogObjectType.query.order_by(
        CatalogObjectType.sort_order.asc(), CatalogObjectType.name.asc()
    ).all()


def get_object_type(type_id: int) -> CatalogObjectType | None:
    return db.session.get(CatalogObjectType, type_id)


def get_object_type_by_slug(slug: str) -> CatalogObjectType | None:
    return CatalogObjectType.query.filter_by(slug=slug).first()


def create_object_type(**fields) -> CatalogObjectType:
    object_type = CatalogObjectType(**fields)
    db.session.add(object_type)
    db.session.commit()
    return object_type


def list_attribute_definitions(type_id: int) -> list[CatalogAttributeDefinition]:
    return (
        CatalogAttributeDefinition.query.filter_by(object_type_id=type_id)
        .order_by(CatalogAttributeDefinition.sort_order.asc())
        .all()
    )


def create_attribute_definition(**fields) -> CatalogAttributeDefinition:
    definition = CatalogAttributeDefinition(**fields)
    db.session.add(definition)
    db.session.commit()
    return definition
