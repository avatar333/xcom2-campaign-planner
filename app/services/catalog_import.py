"""Catalog import/export orchestration (FR-SRC-7/8).

Import never overwrites an existing `(source_slug, template_key)` object or
an existing exact-duplicate relationship -- conflicts are *skipped* and
reported, consistent with `validator.py`'s conflict detection. Callers must
run `validate_document` first and refuse to import when `report.is_valid`
is false; this module trusts that has already happened and additionally
re-checks per-row at commit time (defends against a race between
dry-run and commit, e.g. two imports in quick succession).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.extensions import db
from app.importer.document import CatalogDocument, ObjectTypeRef, SourceRef
from app.importer.validator import validate_document
from app.models import CatalogObject, CatalogRelationship
from app.repositories import catalog_objects as objects_repo
from app.repositories import catalog_relationships as relationships_repo
from app.repositories import content_sources as sources_repo
from app.repositories import object_types as object_types_repo
from app.services.catalog_objects import slugify


@dataclass
class ImportResult:
    sources_created: int = 0
    object_types_created: int = 0
    objects_created: int = 0
    objects_skipped: int = 0
    relationships_created: int = 0
    relationships_skipped: int = 0
    errors: list[str] = field(default_factory=list)


class CatalogImportError(ValueError):
    """Raised when attempting to commit an invalid document."""


def _ensure_source(ref: SourceRef) -> tuple[int, bool]:
    existing = sources_repo.get_content_source_by_slug(ref.slug)
    if existing is not None:
        return existing.id, False
    created = sources_repo.create_content_source(
        slug=ref.slug,
        name=ref.name or ref.slug,
        kind=ref.kind or "user",
        version=ref.version or "0.0.0",
        steam_workshop_id=ref.steam_workshop_id,
        description=ref.description,
        license=ref.license,
        attribution_text=ref.attribution_text,
        homepage_url=ref.homepage_url,
        is_builtin=False,
        is_enabled_by_default=False,
    )
    return created.id, True


def _ensure_object_type(ref: ObjectTypeRef) -> tuple[int, bool]:
    existing = object_types_repo.get_object_type_by_slug(ref.slug)
    if existing is not None:
        return existing.id, False
    from app.models.base import utc_now_iso

    created = object_types_repo.create_object_type(
        slug=ref.slug,
        name=ref.name or ref.slug,
        description=ref.description,
        is_builtin=False,
        created_at=utc_now_iso(),
    )
    return created.id, True


def import_document(document: CatalogDocument) -> ImportResult:
    report = validate_document(document)
    if not report.is_valid:
        raise CatalogImportError(
            "Document has validation errors and cannot be imported: "
            + "; ".join(i.message for i in report.errors)
        )

    result = ImportResult()

    source_id_by_slug: dict[str, int] = {}
    for ref in document.content_sources:
        source_id, created = _ensure_source(ref)
        source_id_by_slug[ref.slug] = source_id
        if created:
            result.sources_created += 1

    type_id_by_slug: dict[str, int] = {}
    for ref in document.object_types:
        type_id, created = _ensure_object_type(ref)
        type_id_by_slug[ref.slug] = type_id
        if created:
            result.object_types_created += 1

    def _resolve_source_id(slug: str) -> int | None:
        if slug in source_id_by_slug:
            return source_id_by_slug[slug]
        existing = sources_repo.get_content_source_by_slug(slug)
        if existing is not None:
            source_id_by_slug[slug] = existing.id
            return existing.id
        return None

    def _resolve_type_id(slug: str) -> int | None:
        if slug in type_id_by_slug:
            return type_id_by_slug[slug]
        existing = object_types_repo.get_object_type_by_slug(slug)
        if existing is not None:
            type_id_by_slug[slug] = existing.id
            return existing.id
        return None

    object_id_by_key: dict[tuple[str, str], int] = {}
    for obj in document.objects:
        source_id = _resolve_source_id(obj.source_slug)
        type_id = _resolve_type_id(obj.type_slug)
        if source_id is None or type_id is None:
            result.errors.append(
                f"Skipped object {obj.source_slug}:{obj.template_key} -- unresolved source/type."
            )
            result.objects_skipped += 1
            continue

        existing = objects_repo.get_by_source_and_template_key(source_id, obj.template_key)
        if existing is not None:
            object_id_by_key[(obj.source_slug, obj.template_key)] = existing.id
            result.objects_skipped += 1
            continue

        base_slug = slugify(obj.slug or obj.name)
        final_slug = base_slug
        suffix = 2
        while objects_repo.get_by_source_and_slug(source_id, final_slug) is not None:
            final_slug = f"{base_slug}-{suffix}"
            suffix += 1

        created_obj = objects_repo.create_object(
            source_id=source_id,
            type_id=type_id,
            template_key=obj.template_key,
            slug=final_slug,
            name=obj.name,
            short_description=obj.short_description,
            long_description=obj.long_description,
            tier=obj.tier,
            attributes=json.dumps(obj.attributes or {}, sort_keys=True),
            is_user_authored=obj.is_user_authored,
        )
        if obj.tags:
            objects_repo.set_tags(created_obj, obj.tags)
        object_id_by_key[(obj.source_slug, obj.template_key)] = created_obj.id
        result.objects_created += 1

    def _resolve_object_id(slug: str, template_key: str) -> int | None:
        key = (slug, template_key)
        if key in object_id_by_key:
            return object_id_by_key[key]
        source_id = _resolve_source_id(slug)
        if source_id is None:
            return None
        existing = objects_repo.get_by_source_and_template_key(source_id, template_key)
        if existing is not None:
            object_id_by_key[key] = existing.id
            return existing.id
        return None

    for rel in document.relationships:
        relationship_type = relationships_repo.get_relationship_type_by_slug(rel.type_slug)
        source_object_id = _resolve_object_id(rel.source_slug, rel.source_template_key)
        target_object_id = _resolve_object_id(rel.target_slug, rel.target_template_key)

        if relationship_type is None or source_object_id is None or target_object_id is None:
            result.errors.append(
                f"Skipped relationship {rel.type_slug} "
                f"{rel.source_slug}:{rel.source_template_key} -> {rel.target_slug}:{rel.target_template_key} "
                "-- unresolved reference."
            )
            result.relationships_skipped += 1
            continue

        duplicate = relationships_repo.find_duplicate(
            relationship_type_id=relationship_type.id,
            source_object_id=source_object_id,
            target_object_id=target_object_id,
            group_key=rel.group_key,
        )
        if duplicate is not None:
            result.relationships_skipped += 1
            continue

        source_object = objects_repo.get_object(source_object_id)
        relationships_repo.create_relationship(
            relationship_type_id=relationship_type.id,
            source_object_id=source_object_id,
            target_object_id=target_object_id,
            group_key=rel.group_key,
            group_logic=rel.group_logic,
            amount=rel.amount,
            unit=rel.unit,
            source_pack_id=source_object.source_id if source_object else source_id_by_slug.get(rel.source_slug),
            is_user_authored=True,
        )
        result.relationships_created += 1

    db.session.commit()
    return result


def export_document(
    *, source_ids: list[int] | None = None, include_builtin_sources: bool = True
) -> CatalogDocument:
    """Export catalog data to the interchange document shape (FR-SRC-8).

    `source_ids=None` exports every source; otherwise only the given
    sources and the objects/relationships that belong to them.
    """
    sources_query = sources_repo.list_content_sources()
    if source_ids is not None:
        sources_query = [s for s in sources_query if s.id in source_ids]
    if not include_builtin_sources:
        sources_query = [s for s in sources_query if not s.is_builtin]

    source_refs = [
        SourceRef(
            slug=s.slug,
            name=s.name,
            kind=s.kind,
            version=s.version,
            steam_workshop_id=s.steam_workshop_id,
            description=s.description,
            license=s.license,
            attribution_text=s.attribution_text,
            homepage_url=s.homepage_url,
        )
        for s in sources_query
    ]

    exported_source_ids = {s.id for s in sources_query}
    objects: list[CatalogObject] = (
        CatalogObject.query.filter(CatalogObject.source_id.in_(exported_source_ids)).all()
        if exported_source_ids
        else []
    )

    source_by_id = {s.id: s for s in sources_query}
    type_ids_used = {o.type_id for o in objects}
    types_used = [
        object_types_repo.get_object_type(tid) for tid in type_ids_used
    ]
    type_refs = [
        ObjectTypeRef(slug=t.slug, name=t.name, description=t.description)
        for t in types_used
        if t is not None
    ]

    from app.importer.document import ObjectRecord
    from app.models import CatalogObjectTag

    object_records = []
    for obj in objects:
        source = source_by_id.get(obj.source_id)
        object_type = object_types_repo.get_object_type(obj.type_id)
        tags = [
            row.tag
            for row in CatalogObjectTag.query.filter_by(object_id=obj.id).all()
        ]
        object_records.append(
            ObjectRecord(
                source_slug=source.slug if source else "",
                template_key=obj.template_key,
                type_slug=object_type.slug if object_type else "",
                name=obj.name,
                slug=obj.slug,
                short_description=obj.short_description,
                long_description=obj.long_description,
                tier=obj.tier,
                tags=tags,
                attributes=json.loads(obj.attributes) if obj.attributes else {},
                is_user_authored=obj.is_user_authored,
            )
        )

    object_by_id = {o.id: o for o in objects}
    relationships: list[CatalogRelationship] = (
        CatalogRelationship.query.filter(
            CatalogRelationship.source_object_id.in_(object_by_id.keys())
        ).all()
        if object_by_id
        else []
    )

    from app.importer.document import RelationshipRecord

    relationship_records = []
    for rel in relationships:
        source_object = object_by_id.get(rel.source_object_id) or objects_repo.get_object(
            rel.source_object_id
        )
        target_object = object_by_id.get(rel.target_object_id) or objects_repo.get_object(
            rel.target_object_id
        )
        if source_object is None or target_object is None:
            continue
        rel_type = relationships_repo.get_relationship_type(rel.relationship_type_id)
        source_src = source_by_id.get(source_object.source_id) or sources_repo.get_content_source(
            source_object.source_id
        )
        target_src = source_by_id.get(target_object.source_id) or sources_repo.get_content_source(
            target_object.source_id
        )
        relationship_records.append(
            RelationshipRecord(
                type_slug=rel_type.slug if rel_type else "",
                source_slug=source_src.slug if source_src else "",
                source_template_key=source_object.template_key,
                target_slug=target_src.slug if target_src else "",
                target_template_key=target_object.template_key,
                group_key=rel.group_key,
                group_logic=rel.group_logic,
                amount=float(rel.amount) if rel.amount is not None else None,
                unit=rel.unit,
            )
        )

    return CatalogDocument(
        content_sources=source_refs,
        object_types=type_refs,
        objects=object_records,
        relationships=relationship_records,
    )
