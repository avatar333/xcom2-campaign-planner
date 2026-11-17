"""Catalog object lifecycle service (FR-CAT-1/2/6/7/8).

Identity rule (DOMAIN_MODEL.md §5): a catalog object's identity is
`(source_id, template_key)`, never `template_key` alone -- this service is
where that uniqueness is enforced for manually-created objects (the
importer, Phase 3's remaining future work, enforces it again at the DB
`UNIQUE` constraint for pack-authored objects).
"""
from __future__ import annotations

import json
import re

from app.domain.attributes import (
    AttributeDefinition,
    dump_attributes_json,
    validate_attributes,
)
from app.models import CatalogObject, ContentSource
from app.repositories import catalog_objects as repo
from app.repositories import content_sources as sources_repo
from app.repositories import object_types as object_types_repo

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class CatalogObjectValidationError(ValueError):
    pass


def slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "object"


def _unique_slug(source_id: int, base_slug: str) -> str:
    slug = base_slug
    suffix = 2
    while repo.get_by_source_and_slug(source_id, slug) is not None:
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def _unique_template_key(source_id: int, base_key: str) -> str:
    key = base_key
    suffix = 2
    while repo.get_by_source_and_template_key(source_id, key) is not None:
        key = f"{base_key}-{suffix}"
        suffix += 1
    return key


def _attribute_definitions_for_type(type_id: int) -> list[AttributeDefinition]:
    return [
        AttributeDefinition(
            key=d.key,
            data_type=d.data_type,
            enum_values=(json.loads(d.enum_values) if d.enum_values else None),
            unit=d.unit,
            is_required=d.is_required,
            default_value=(json.loads(d.default_value) if d.default_value else None),
        )
        for d in object_types_repo.list_attribute_definitions(type_id)
    ]


def _validate_core_fields(name: str, source: ContentSource, type_id: int) -> str:
    name = (name or "").strip()
    if not name:
        raise CatalogObjectValidationError("Name is required.")
    if len(name) > 200:
        raise CatalogObjectValidationError("Name must be 200 characters or fewer.")
    if source is None:
        raise CatalogObjectValidationError("A content source is required.")
    if object_types_repo.get_object_type(type_id) is None:
        raise CatalogObjectValidationError("Invalid object type.")
    return name


def create_object(
    *,
    source_id: int,
    type_id: int,
    name: str,
    template_key: str | None = None,
    slug: str | None = None,
    short_description: str | None = None,
    long_description: str | None = None,
    tier: int | None = None,
    tags: list[str] | None = None,
    attributes: dict | None = None,
    is_user_authored: bool = False,
) -> CatalogObject:
    source = sources_repo.get_content_source(source_id)
    name = _validate_core_fields(name, source, type_id)

    base_slug = slugify(slug or name)
    final_slug = _unique_slug(source_id, base_slug)

    base_template_key = (template_key or "").strip() or base_slug
    final_template_key = _unique_template_key(source_id, base_template_key)

    definitions = _attribute_definitions_for_type(type_id)
    try:
        validated_attributes = validate_attributes(definitions, attributes or {})
    except ValueError as exc:
        raise CatalogObjectValidationError(str(exc)) from exc

    obj = repo.create_object(
        source_id=source_id,
        type_id=type_id,
        template_key=final_template_key,
        slug=final_slug,
        name=name,
        short_description=(short_description or "").strip() or None,
        long_description=(long_description or "").strip() or None,
        tier=tier,
        attributes=dump_attributes_json(validated_attributes),
        is_user_authored=is_user_authored,
    )
    if tags:
        repo.set_tags(obj, tags)
    return obj


def update_object(
    obj: CatalogObject,
    *,
    name: str,
    short_description: str | None = None,
    long_description: str | None = None,
    tier: int | None = None,
    tags: list[str] | None = None,
    attributes: dict | None = None,
) -> CatalogObject:
    source = sources_repo.get_content_source(obj.source_id)
    name = _validate_core_fields(name, source, obj.type_id)

    definitions = _attribute_definitions_for_type(obj.type_id)
    try:
        validated_attributes = validate_attributes(definitions, attributes or {})
    except ValueError as exc:
        raise CatalogObjectValidationError(str(exc)) from exc

    repo.update_object(
        obj,
        name=name,
        short_description=(short_description or "").strip() or None,
        long_description=(long_description or "").strip() or None,
        tier=tier,
        attributes=dump_attributes_json(validated_attributes),
    )
    if tags is not None:
        repo.set_tags(obj, tags)
    return obj


class CatalogObjectDeletionError(ValueError):
    pass


def delete_object(obj: CatalogObject) -> None:
    """Hard-delete only if nothing references this object (FR-CAT-8:
    objects referenced by campaign state are *retired*, never deleted)."""
    if repo.is_referenced_by_campaign_state(obj):
        raise CatalogObjectDeletionError(
            "This object is referenced by at least one campaign and cannot be deleted. "
            "It will be retired instead."
        )
    if repo.is_referenced_by_relationship(obj):
        raise CatalogObjectDeletionError(
            "This object has relationships to other objects. Remove them first, "
            "or retire the object instead of deleting it."
        )
    repo.delete_object(obj)


def retire_or_delete(obj: CatalogObject) -> str:
    """Delete if safe, otherwise retire. Returns 'deleted' or 'retired'."""
    if repo.is_referenced_by_campaign_state(obj) or repo.is_referenced_by_relationship(obj):
        repo.retire_object(obj)
        return "retired"
    repo.delete_object(obj)
    return "deleted"


def create_overlay(
    base_object: CatalogObject,
    *,
    user_source_id: int,
    name: str | None = None,
    short_description: str | None = None,
    long_description: str | None = None,
    tier: int | None = None,
    attributes_patch: dict | None = None,
) -> CatalogObject:
    """Create a user-source overlay of a built-in/imported object (FR-CAT-6).

    The original row is never mutated; this inserts a new object in the
    user's source with `overrides_object_id` pointing at `base_object`, so
    the effective-catalog resolver (ADR-0013) can later merge/patch it.
    """
    patch: dict = {}
    if name:
        patch["name"] = name.strip()
    if short_description is not None:
        patch["short_description"] = short_description.strip() or None
    if long_description is not None:
        patch["long_description"] = long_description.strip() or None
    if tier is not None:
        patch["tier"] = tier
    if attributes_patch:
        definitions = _attribute_definitions_for_type(base_object.type_id)
        try:
            validated_patch = validate_attributes(definitions, attributes_patch)
        except ValueError as exc:
            raise CatalogObjectValidationError(str(exc)) from exc
        patch["attributes"] = validated_patch

    overlay_name = name or f"{base_object.name} (edited)"
    base_slug = slugify(f"{base_object.slug}-overlay")
    final_slug = _unique_slug(user_source_id, base_slug)
    final_template_key = _unique_template_key(user_source_id, f"{base_object.template_key}-overlay")

    obj = repo.create_object(
        source_id=user_source_id,
        type_id=base_object.type_id,
        template_key=final_template_key,
        slug=final_slug,
        name=overlay_name,
        short_description=base_object.short_description,
        long_description=base_object.long_description,
        tier=base_object.tier,
        attributes=base_object.attributes,
        overrides_object_id=base_object.id,
        patch=json.dumps(patch, sort_keys=True),
        is_user_authored=True,
    )
    return obj
