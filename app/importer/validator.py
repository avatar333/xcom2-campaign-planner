"""Dry-run validation + conflict detection for catalog interchange documents
(FR-SRC-7). Pure w.r.t. the database: it only *reads* existing rows to
detect conflicts, never writes -- the actual import/commit step is
`app/services/catalog_import.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.importer.document import CatalogDocument, ObjectRecord, RelationshipRecord
from app.repositories import catalog_objects as objects_repo
from app.repositories import catalog_relationships as relationships_repo
from app.repositories import content_sources as sources_repo
from app.repositories import object_types as object_types_repo


@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning"
    message: str
    context: str | None = None


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)
    new_sources: int = 0
    new_object_types: int = 0
    new_objects: int = 0
    conflicting_objects: int = 0
    new_relationships: int = 0
    conflicting_relationships: int = 0

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _object_key(obj: ObjectRecord) -> tuple[str, str]:
    return (obj.source_slug, obj.template_key)


def validate_document(document: CatalogDocument) -> ValidationReport:
    report = ValidationReport()

    declared_source_slugs = {s.slug for s in document.content_sources}
    declared_type_slugs = {t.slug for t in document.object_types}

    for source in document.content_sources:
        if sources_repo.get_content_source_by_slug(source.slug) is not None:
            report.issues.append(
                ValidationIssue(
                    "warning",
                    f"Content source '{source.slug}' already exists; it will not be re-created.",
                    context=source.slug,
                )
            )
        else:
            report.new_sources += 1

    for object_type in document.object_types:
        if object_types_repo.get_object_type_by_slug(object_type.slug) is not None:
            report.issues.append(
                ValidationIssue(
                    "warning",
                    f"Object type '{object_type.slug}' already exists; it will not be re-created.",
                    context=object_type.slug,
                )
            )
        else:
            report.new_object_types += 1

    seen_object_keys: set[tuple[str, str]] = set()
    for obj in document.objects:
        key = _object_key(obj)
        if key in seen_object_keys:
            report.issues.append(
                ValidationIssue(
                    "error",
                    f"Duplicate object in document: source '{obj.source_slug}', template_key '{obj.template_key}'.",
                    context=f"{obj.source_slug}:{obj.template_key}",
                )
            )
            continue
        seen_object_keys.add(key)

        if not obj.name or not obj.name.strip():
            report.issues.append(
                ValidationIssue("error", "Object is missing a name.", context=f"{obj.source_slug}:{obj.template_key}")
            )

        if obj.source_slug not in declared_source_slugs and sources_repo.get_content_source_by_slug(
            obj.source_slug
        ) is None:
            report.issues.append(
                ValidationIssue(
                    "error",
                    f"Object references unknown content source '{obj.source_slug}'.",
                    context=f"{obj.source_slug}:{obj.template_key}",
                )
            )

        if obj.type_slug not in declared_type_slugs and object_types_repo.get_object_type_by_slug(
            obj.type_slug
        ) is None:
            report.issues.append(
                ValidationIssue(
                    "error",
                    f"Object references unknown object type '{obj.type_slug}'.",
                    context=f"{obj.source_slug}:{obj.template_key}",
                )
            )

        existing_source = sources_repo.get_content_source_by_slug(obj.source_slug)
        if existing_source is not None:
            existing_object = objects_repo.get_by_source_and_template_key(
                existing_source.id, obj.template_key
            )
            if existing_object is not None:
                report.conflicting_objects += 1
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        f"Object '{obj.template_key}' already exists in source '{obj.source_slug}' "
                        "and will be skipped (not overwritten).",
                        context=f"{obj.source_slug}:{obj.template_key}",
                    )
                )
            else:
                report.new_objects += 1
        else:
            report.new_objects += 1

    for rel in document.relationships:
        _validate_relationship(rel, declared_source_slugs, seen_object_keys, report)

    return report


def _validate_relationship(
    rel: RelationshipRecord,
    declared_source_slugs: set[str],
    seen_object_keys: set[tuple[str, str]],
    report: ValidationReport,
) -> None:
    context = f"{rel.source_slug}:{rel.source_template_key} -> {rel.target_slug}:{rel.target_template_key}"

    if (
        rel.source_slug == rel.target_slug
        and rel.source_template_key == rel.target_template_key
    ):
        report.issues.append(
            ValidationIssue("error", "A relationship cannot connect an object to itself.", context=context)
        )
        return

    if relationships_repo.get_relationship_type_by_slug(rel.type_slug) is None:
        report.issues.append(
            ValidationIssue("error", f"Unknown relationship type '{rel.type_slug}'.", context=context)
        )

    source_known = (rel.source_slug, rel.source_template_key) in seen_object_keys or (
        _resolve_existing_object(rel.source_slug, rel.source_template_key) is not None
    )
    target_known = (rel.target_slug, rel.target_template_key) in seen_object_keys or (
        _resolve_existing_object(rel.target_slug, rel.target_template_key) is not None
    )
    if not source_known:
        report.issues.append(
            ValidationIssue(
                "error",
                f"Relationship source object '{rel.source_slug}:{rel.source_template_key}' not found.",
                context=context,
            )
        )
    if not target_known:
        report.issues.append(
            ValidationIssue(
                "error",
                f"Relationship target object '{rel.target_slug}:{rel.target_template_key}' not found.",
                context=context,
            )
        )

    if source_known and target_known:
        report.new_relationships += 1


def _resolve_existing_object(source_slug: str, template_key: str):
    source = sources_repo.get_content_source_by_slug(source_slug)
    if source is None:
        return None
    return objects_repo.get_by_source_and_template_key(source.id, template_key)
