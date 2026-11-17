"""Dry-run validation for curated content packs (Phase 4, brief "Validation").

Pure w.r.t. the database: only reads existing rows to detect conflicts and
check referential integrity; writing/committing is
`app/services/content_import.py`. Produces a human-readable diagnostics
report, per the brief's "Generate an ingestion validation report."
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.importer.pack_schema import (
    PackDocument,
    PackObjectRecord,
    PackPrerequisiteGroupNode,
    PackRelationshipRecord,
)
from app.repositories import catalog_objects as objects_repo
from app.repositories import catalog_relationships as relationships_repo
from app.repositories import content_sources as sources_repo
from app.repositories import object_types as object_types_repo


@dataclass
class PackValidationIssue:
    severity: str  # "error" | "warning"
    message: str
    context: str | None = None


@dataclass
class PackValidationReport:
    issues: list[PackValidationIssue] = field(default_factory=list)
    new_objects: int = 0
    updated_objects: int = 0
    retired_objects: int = 0
    new_relationships: int = 0
    new_prerequisite_groups: int = 0
    new_stat_profiles: int = 0
    new_asset_manifest_entries: int = 0

    @property
    def errors(self) -> list[PackValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[PackValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _object_identity_key(slug_fallback: str, obj: PackObjectRecord) -> tuple[str, str]:
    return (slug_fallback, obj.template_key)


def validate_pack(document: PackDocument, *, is_dry_run: bool = True) -> PackValidationReport:
    """Validate a loaded pack document against currently-imported data.

    Checks (brief "Validation" list):
    - every relationship endpoint exists (declared in this pack or already imported)
    - every object references an existing/declared content source and object type
    - stable IDs are unique in the intended namespace (source_slug, template_key)
    - no impossible self-prerequisites
    - prerequisite groups are structurally valid
    - no unexpected duplicate template/source identities within the pack
    """
    report = PackValidationReport()
    own_slug = document.manifest.slug

    declared_sources = {own_slug, *document.manifest.requires, *document.manifest.extends}
    declared_type_slugs = {t.slug for t in document.object_types}
    declared_rel_type_slugs = {t.slug for t in document.relationship_types}

    for required_slug in document.manifest.requires:
        if required_slug != own_slug and sources_repo.get_content_source_by_slug(required_slug) is None:
            report.issues.append(
                PackValidationIssue(
                    "error",
                    f"Pack requires '{required_slug}', which has not been imported yet.",
                    context="pack.json.requires",
                )
            )

    for extends_slug in document.manifest.extends:
        if extends_slug != own_slug and sources_repo.get_content_source_by_slug(extends_slug) is None:
            report.issues.append(
                PackValidationIssue(
                    "error",
                    f"Pack extends '{extends_slug}', which has not been imported yet.",
                    context="pack.json.extends",
                )
            )

    for conflict_slug in document.manifest.conflicts:
        conflicting_source = sources_repo.get_content_source_by_slug(conflict_slug)
        if conflicting_source is not None:
            report.issues.append(
                PackValidationIssue(
                    "warning",
                    f"Pack conflicts with already-imported source '{conflict_slug}'; "
                    "a campaign must not enable both.",
                    context="pack.json.conflicts",
                )
            )

    seen_keys: set[tuple[str, str]] = set()
    for obj in document.objects:
        key = _object_identity_key(own_slug, obj)
        context = f"objects:{obj.template_key}"
        if key in seen_keys:
            report.issues.append(
                PackValidationIssue(
                    "error", f"Duplicate object identity within pack: {key}.", context=context
                )
            )
            continue
        seen_keys.add(key)

        if not obj.name.strip():
            report.issues.append(PackValidationIssue("error", "Object is missing a name.", context=context))

        if obj.type_slug not in declared_type_slugs and object_types_repo.get_object_type_by_slug(
            obj.type_slug
        ) is None:
            report.issues.append(
                PackValidationIssue(
                    "error", f"Object references unknown object type '{obj.type_slug}'.", context=context
                )
            )

        for ref_kind, ref in (("overrides", obj.overrides), ("replaces", obj.replaces)):
            if ref is None:
                continue
            ref_source_slug, _ref_key = ref
            if ref_source_slug not in declared_sources:
                report.issues.append(
                    PackValidationIssue(
                        "error",
                        f"Object {ref_kind} an object in source '{ref_source_slug}', which this pack "
                        f"does not declare in requires/extends.",
                        context=context,
                    )
                )

        existing_source = sources_repo.get_content_source_by_slug(own_slug)
        existing_object = None
        if existing_source is not None:
            existing_object = objects_repo.get_by_source_and_template_key(
                existing_source.id, obj.template_key
            )
        if existing_object is not None:
            report.updated_objects += 1
        else:
            report.new_objects += 1
        if obj.is_retired:
            report.retired_objects += 1

    for rel in document.relationships:
        _validate_relationship(rel, declared_rel_type_slugs, seen_keys, declared_sources, report)

    for group in document.prerequisite_groups:
        _validate_group_node(
            group.root,
            seen_keys,
            declared_sources,
            report,
            context=f"prerequisite_groups:{group.target}",
        )
        if group.relationship_type_slug not in declared_rel_type_slugs and relationships_repo.get_relationship_type_by_slug(
            group.relationship_type_slug
        ) is None:
            report.issues.append(
                PackValidationIssue(
                    "error",
                    f"Prerequisite group references unknown relationship type '{group.relationship_type_slug}'.",
                    context=f"prerequisite_groups:{group.target}",
                )
            )
        report.new_prerequisite_groups += 1

    for profile in document.stat_profiles:
        report.new_stat_profiles += 1

    for asset_entry in document.asset_manifest:
        report.new_asset_manifest_entries += 1

    return report


def _validate_relationship(
    rel: PackRelationshipRecord,
    declared_rel_type_slugs: set[str],
    seen_keys: set[tuple[str, str]],
    declared_sources: set[str],
    report: PackValidationReport,
) -> None:
    context = f"relationships:{rel.source}->{rel.target}"

    if rel.source == rel.target:
        report.issues.append(
            PackValidationIssue("error", "A relationship cannot connect an object to itself.", context=context)
        )
        return

    if rel.type_slug not in declared_rel_type_slugs and relationships_repo.get_relationship_type_by_slug(
        rel.type_slug
    ) is None:
        report.issues.append(
            PackValidationIssue("error", f"Unknown relationship type '{rel.type_slug}'.", context=context)
        )

    for endpoint_name, endpoint in (("source", rel.source), ("target", rel.target)):
        if not _endpoint_resolvable(endpoint, seen_keys, declared_sources):
            report.issues.append(
                PackValidationIssue(
                    "error", f"Relationship {endpoint_name} object {endpoint} not found.", context=context
                )
            )
        else:
            report.new_relationships += 1 if endpoint_name == "target" else 0


def _validate_group_node(
    node: PackPrerequisiteGroupNode,
    seen_keys: set[tuple[str, str]],
    declared_sources: set[str],
    report: PackValidationReport,
    *,
    context: str,
) -> None:
    if not node.members:
        report.issues.append(
            PackValidationIssue("error", "Prerequisite group has no members.", context=context)
        )
        return
    for member in node.members:
        if isinstance(member, PackPrerequisiteGroupNode):
            _validate_group_node(member, seen_keys, declared_sources, report, context=context)
        else:
            if not _endpoint_resolvable(member, seen_keys, declared_sources):
                report.issues.append(
                    PackValidationIssue(
                        "error", f"Prerequisite group member {member} not found.", context=context
                    )
                )


def _endpoint_resolvable(
    endpoint: tuple[str, str], seen_keys: set[tuple[str, str]], declared_sources: set[str]
) -> bool:
    """An endpoint resolves if it's declared within this pack, or if its
    source already exists and already has that object imported -- this
    deliberately allows referencing another already-imported source's
    object without that source being in `requires`/`extends` as long as
    it genuinely exists (e.g. a resource object), while `declared_sources`
    itself is used by callers for the stricter overrides/replaces check."""
    del declared_sources  # only `overrides`/`replaces` enforce the strict declared-source check
    if endpoint in seen_keys:
        return True
    source_slug, template_key = endpoint
    source = sources_repo.get_content_source_by_slug(source_slug)
    if source is None:
        return False
    return objects_repo.get_by_source_and_template_key(source.id, template_key) is not None
