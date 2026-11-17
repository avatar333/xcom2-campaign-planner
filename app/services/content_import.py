"""Curated content-pack import orchestration (`ARCHITECTURE.md` §5 Phase 4;
Phase 4 brief "Database ingestion").

Distinct from Phase 3's `app/services/catalog_import.py` (`ADR-0017`, the
ad hoc UI import/export document format) -- this module is the maintainer
tool that loads `content/packs/<slug>/` directories (curated XCOM
2/WotC/LWotC facts, `docs/DATA_SOURCING.md`) into the database.

Guarantees upheld here (Phase 4 brief, "Database ingestion" + "Upgrades and
source overrides"):
- Never destroys campaign-specific data (no code path here touches any
  `campaign_*` table at all).
- Never overwrites a user-created/mod object: only rows belonging to the
  importing pack's own `source_id` are ever upserted; everything else is
  resolved read-only (`get_by_source_and_template_key`).
- Idempotent: unchanged pack content (by checksum) is a no-op; re-importing
  a changed pack upserts in place rather than duplicating.
- Objects/relationships disappearing from a newer pack version are
  retired, not deleted, if any campaign state (or catalog relationship)
  references them -- mirrors `app/services/catalog_objects.retire_or_delete`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.extensions import db
from app.importer.pack_schema import (
    PackDocument,
    PackPrerequisiteGroupNode,
    compute_pack_checksum,
    load_pack,
)
from app.importer.pack_validator import validate_pack
from app.models import CatalogObject
from app.models.base import utc_now_iso
from app.repositories import catalog_objects as objects_repo
from app.repositories import catalog_relationships as relationships_repo
from app.repositories import content_sources as sources_repo
from app.repositories import object_types as object_types_repo
from app.repositories import prerequisite_groups as groups_repo
from app.repositories import soldier_stat_profiles as stat_profiles_repo
from app.services.catalog_objects import slugify


class ContentPackImportError(ValueError):
    """Raised when attempting to commit an invalid pack, or a precondition fails."""


@dataclass
class ContentPackImportResult:
    source_slug: str
    checksum: str
    already_up_to_date: bool = False
    object_types_created: int = 0
    relationship_types_created: int = 0
    objects_created: int = 0
    objects_updated: int = 0
    objects_retired: int = 0
    relationships_created: int = 0
    prerequisite_groups_created: int = 0
    stat_profiles_created: int = 0
    asset_manifest_entries_created: int = 0
    errors: list[str] = field(default_factory=list)


def _ensure_source_row(document: PackDocument):
    manifest = document.manifest
    existing = sources_repo.get_content_source_by_slug(manifest.slug)
    fields = dict(
        name=manifest.name,
        kind=manifest.kind,
        version=manifest.version,
        steam_workshop_id=manifest.steam_workshop_id,
        description=manifest.description,
        license=manifest.license,
        attribution_text=manifest.attribution_text,
        homepage_url=manifest.homepage_url,
        source_repository=manifest.source_repository,
        source_ref=manifest.source_ref,
        source_commit_sha=manifest.source_commit_sha,
    )
    if existing is not None:
        return sources_repo.update_content_source(existing, **fields)
    return sources_repo.create_content_source(
        slug=manifest.slug, is_builtin=True, is_enabled_by_default=(manifest.kind in ("base", "dlc")), **fields
    )


def _ensure_relations(document: PackDocument, source_id: int) -> None:
    relation_pairs: list[tuple[str, int]] = []
    for relation_kind, slugs in (
        ("requires", document.manifest.requires),
        ("extends", document.manifest.extends),
        ("conflicts", document.manifest.conflicts),
    ):
        for related_slug in slugs:
            related = sources_repo.get_content_source_by_slug(related_slug)
            if related is not None:
                relation_pairs.append((relation_kind, related.id))
    source = sources_repo.get_content_source(source_id)
    sources_repo.set_relations(source, relation_pairs)


def _ensure_object_types(document: PackDocument) -> int:
    created = 0
    for type_def in document.object_types:
        object_type = object_types_repo.get_object_type_by_slug(type_def.slug)
        if object_type is None:
            object_type = object_types_repo.create_object_type(
                slug=type_def.slug,
                name=type_def.name,
                description=type_def.description,
                is_builtin=False,
                created_at=utc_now_iso(),
            )
            created += 1
        _ensure_attribute_definitions(object_type.id, type_def.attributes)
    return created


def _ensure_attribute_definitions(object_type_id: int, attribute_defs: list[dict]) -> None:
    """Add any attribute keys declared by a pack for `object_type_id` that
    are not already defined -- this is how a pack can extend a type that
    another pack (or the Phase 2 seed) already introduced, e.g. the WotC
    pack adding a `wotc_available` attribute to the already-seeded
    `facility` type, without duplicating or erroring on the shared keys."""
    existing_keys = {d.key for d in object_types_repo.list_attribute_definitions(object_type_id)}
    for attr in attribute_defs:
        if attr["key"] in existing_keys:
            continue
        object_types_repo.create_attribute_definition(
            object_type_id=object_type_id,
            key=attr["key"],
            data_type=attr["data_type"],
            enum_values=json.dumps(attr["enum_values"]) if attr.get("enum_values") else None,
            unit=attr.get("unit"),
            is_required=bool(attr.get("is_required", False)),
            default_value=json.dumps(attr["default_value"]) if "default_value" in attr else None,
            display_hint=attr.get("display_hint"),
            sort_order=attr.get("sort_order", 0),
        )


def _ensure_relationship_types(document: PackDocument) -> int:
    created = 0
    by_slug = {rt.slug: rt for rt in relationships_repo.list_relationship_types()}
    for type_def in document.relationship_types:
        if type_def.slug in by_slug:
            continue
        rt = relationships_repo.get_relationship_type_by_slug(type_def.slug)
        if rt is None:
            from app.models import CatalogRelationshipType

            rt = CatalogRelationshipType(
                slug=type_def.slug, name=type_def.name, is_quantified=type_def.is_quantified, is_builtin=False
            )
            db.session.add(rt)
            db.session.flush()
            created += 1
        by_slug[type_def.slug] = rt
    for type_def in document.relationship_types:
        if type_def.inverse_slug and type_def.inverse_slug in by_slug:
            by_slug[type_def.slug].inverse_type_id = by_slug[type_def.inverse_slug].id
    return created


def import_pack(pack_dir: str, *, force: bool = False) -> ContentPackImportResult:
    """Load, validate and commit one curated content pack.

    `force=True` re-imports even if the checksum matches (useful for a
    "refresh" command that re-applies after manual edits without bumping
    the version). Raises `ContentPackImportError` if validation fails.
    """
    document = load_pack(pack_dir)
    checksum = compute_pack_checksum(pack_dir)
    result = ContentPackImportResult(source_slug=document.manifest.slug, checksum=checksum)

    existing_source = sources_repo.get_content_source_by_slug(document.manifest.slug)
    if existing_source is not None and existing_source.import_checksum == checksum and not force:
        result.already_up_to_date = True
        return result

    report = validate_pack(document)
    if not report.is_valid:
        raise ContentPackImportError(
            f"Pack '{document.manifest.slug}' has validation errors: "
            + "; ".join(i.message for i in report.errors)
        )

    source = _ensure_source_row(document)
    _ensure_relations(document, source.id)

    result.object_types_created = _ensure_object_types(document)
    result.relationship_types_created = _ensure_relationship_types(document)

    def _resolve_source_id(slug: str) -> int | None:
        if slug == document.manifest.slug:
            return source.id
        other = sources_repo.get_content_source_by_slug(slug)
        return other.id if other else None

    def _resolve_type_id(slug: str) -> int | None:
        t = object_types_repo.get_object_type_by_slug(slug)
        return t.id if t else None

    def _resolve_object_id(pair: tuple[str, str]) -> int | None:
        slug, template_key = pair
        source_id = _resolve_source_id(slug)
        if source_id is None:
            return None
        obj = objects_repo.get_by_source_and_template_key(source_id, template_key)
        return obj.id if obj else None

    # Pass 1: upsert objects belonging to THIS pack's own source only --
    # never writes to any other source's rows (user/mod content safety).
    object_id_by_template_key: dict[str, int] = {}
    seen_template_keys: set[str] = set()
    for obj in document.objects:
        seen_template_keys.add(obj.template_key)
        type_id = _resolve_type_id(obj.type_slug)
        if type_id is None:
            result.errors.append(f"Skipped object {obj.template_key}: unknown type '{obj.type_slug}'.")
            continue

        existing_obj = objects_repo.get_by_source_and_template_key(source.id, obj.template_key)

        overrides_object_id = _resolve_object_id(obj.overrides) if obj.overrides else None
        replaces_object_id = _resolve_object_id(obj.replaces) if obj.replaces else None

        attributes_json = json.dumps(obj.attributes or {}, sort_keys=True)
        patch_json = json.dumps(obj.patch, sort_keys=True) if obj.patch else None

        if existing_obj is not None:
            objects_repo.update_object(
                existing_obj,
                name=obj.name,
                short_description=obj.short_description,
                long_description=obj.long_description,
                tier=obj.tier,
                attributes=attributes_json,
                overrides_object_id=overrides_object_id,
                replaces_object_id=replaces_object_id,
                patch=patch_json,
                is_retired=obj.is_retired,
            )
            created_obj = existing_obj
            result.objects_updated += 1
        else:
            base_slug = slugify(obj.slug or obj.name)
            final_slug = base_slug
            suffix = 2
            while objects_repo.get_by_source_and_slug(source.id, final_slug) is not None:
                final_slug = f"{base_slug}-{suffix}"
                suffix += 1
            created_obj = objects_repo.create_object(
                source_id=source.id,
                type_id=type_id,
                template_key=obj.template_key,
                slug=final_slug,
                name=obj.name,
                short_description=obj.short_description,
                long_description=obj.long_description,
                tier=obj.tier,
                attributes=attributes_json,
                overrides_object_id=overrides_object_id,
                replaces_object_id=replaces_object_id,
                patch=patch_json,
                is_retired=obj.is_retired,
                is_user_authored=False,
            )
            result.objects_created += 1

        if obj.tags:
            objects_repo.set_tags(created_obj, obj.tags)
        object_id_by_template_key[obj.template_key] = created_obj.id

    # Retire objects that belong to this source but disappeared from the
    # new pack version -- never delete, per ADR-0005/FR-CAT-8, and never
    # touch another source's rows. If a campaign still references the
    # object (state/plan-item/loadout), it is kept active and flagged
    # `is_orphaned_upstream` instead of hidden (`ARCHITECTURE.md` §5.4
    # step 4), so existing campaigns don't lose resolvable objects.
    all_existing_for_source = CatalogObject.query.filter_by(source_id=source.id).all()
    for existing_obj in all_existing_for_source:
        if existing_obj.template_key not in seen_template_keys and not existing_obj.is_retired:
            if objects_repo.is_referenced_by_campaign_state(existing_obj):
                objects_repo.orphan_object(existing_obj)
            else:
                objects_repo.retire_object(existing_obj)
            result.objects_retired += 1

    # Pass 2: relationships (flat AND/OR, ADR-0004).
    for rel in document.relationships:
        rel_type = relationships_repo.get_relationship_type_by_slug(rel.type_slug)
        source_object_id = _resolve_object_id(rel.source)
        target_object_id = _resolve_object_id(rel.target)
        if rel_type is None or source_object_id is None or target_object_id is None:
            result.errors.append(f"Skipped relationship {rel.source}->{rel.target}: unresolved reference.")
            continue
        duplicate = relationships_repo.find_duplicate(
            relationship_type_id=rel_type.id,
            source_object_id=source_object_id,
            target_object_id=target_object_id,
            group_key=rel.group_key,
        )
        if duplicate is not None:
            continue
        relationships_repo.create_relationship(
            relationship_type_id=rel_type.id,
            source_object_id=source_object_id,
            target_object_id=target_object_id,
            group_key=rel.group_key,
            group_logic=rel.group_logic,
            amount=rel.amount,
            unit=rel.unit,
            source_pack_id=source.id,
            is_user_authored=False,
        )
        result.relationships_created += 1

    # Pass 3: compound prerequisite groups (ADR-0018).
    for group_record in document.prerequisite_groups:
        target_object_id = _resolve_object_id(group_record.target)
        rel_type = relationships_repo.get_relationship_type_by_slug(group_record.relationship_type_slug)
        if target_object_id is None or rel_type is None:
            result.errors.append(
                f"Skipped prerequisite group for {group_record.target}: unresolved reference."
            )
            continue
        _create_group_tree(
            group_record.root,
            target_object_id=target_object_id,
            relationship_type_id=rel_type.id,
            source_pack_id=source.id,
            parent_group_id=None,
            resolve_object_id=_resolve_object_id,
        )
        result.prerequisite_groups_created += 1

    # Pass 4: soldier stat profiles.
    for profile in document.stat_profiles:
        target_object_id = _resolve_object_id(profile.target)
        if target_object_id is None:
            result.errors.append(f"Skipped stat profile for {profile.target}: unresolved reference.")
            continue
        stat_profiles_repo.upsert_profile(
            catalog_object_id=target_object_id,
            difficulty=profile.difficulty,
            stat_key=profile.stat_key,
            stat_min=profile.stat_min,
            stat_default=profile.stat_default,
            stat_max=profile.stat_max,
            is_randomized=profile.is_randomized,
            source_pack_id=source.id,
        )
        result.stat_profiles_created += 1

    # Pass 5: asset manifest entries (brief "asset manifest"). If the pack
    # bundles its own locally-authored, redistribution-clear image (under
    # the pack's `assets/` directory, `local_path`), import those bytes
    # now (`origin='pack_bundled'`); otherwise record a `pending` manifest
    # row capturing provenance/intent without bundling a copyrighted
    # binary (`ADR-0008`), ready for the asset-importer seam (brief "Asset
    # importer seam") to fill in from the user's own local game files.
    from app.services import asset_service

    for asset_entry in document.asset_manifest:
        target_object_id = _resolve_object_id(asset_entry.target)
        if target_object_id is None:
            result.errors.append(f"Skipped asset manifest entry for {asset_entry.target}: unresolved reference.")
            continue
        if asset_entry.local_path and document.pack_dir is not None:
            local_file = document.pack_dir / asset_entry.local_path
            if local_file.is_file():
                asset_service.import_local_image_for_object(
                    target_object_id,
                    role=asset_entry.role,
                    file_path=str(local_file),
                    source_url=asset_entry.source_url,
                    source_name=asset_entry.source_name,
                    license=asset_entry.license,
                    attribution_text=asset_entry.attribution_text,
                    redistribution_allowed=asset_entry.redistribution_allowed,
                    import_status="imported",
                )
            else:
                result.errors.append(
                    f"Asset manifest entry for {asset_entry.target} references missing local file "
                    f"'{asset_entry.local_path}'."
                )
                continue
        else:
            asset_service.record_pending_asset_manifest_entry(
                target_object_id,
                role=asset_entry.role,
                source_url=asset_entry.source_url,
                source_name=asset_entry.source_name,
                original_filename=asset_entry.original_filename,
                license=asset_entry.license,
                attribution_text=asset_entry.attribution_text,
                redistribution_allowed=asset_entry.redistribution_allowed,
            )
        result.asset_manifest_entries_created += 1

    source.import_checksum = checksum
    source.imported_at = utc_now_iso()
    db.session.commit()

    from app.services import effective_catalog as effective_catalog_service

    effective_catalog_service.bump_version_for_sources([source.id])

    return result


def _create_group_tree(
    node: PackPrerequisiteGroupNode,
    *,
    target_object_id: int,
    relationship_type_id: int,
    source_pack_id: int,
    parent_group_id: int | None,
    resolve_object_id,
) -> int:
    group = groups_repo.create_group(
        target_object_id=target_object_id,
        parent_group_id=parent_group_id,
        logic=node.logic,
        relationship_type_id=relationship_type_id,
        source_pack_id=source_pack_id,
    )
    for sort_order, member in enumerate(node.members):
        if isinstance(member, PackPrerequisiteGroupNode):
            child_group_id = _create_group_tree(
                member,
                target_object_id=target_object_id,
                relationship_type_id=relationship_type_id,
                source_pack_id=source_pack_id,
                parent_group_id=group.id,
                resolve_object_id=resolve_object_id,
            )
            groups_repo.add_member(group_id=group.id, child_group_id=child_group_id, sort_order=sort_order)
        else:
            object_id = resolve_object_id(member)
            if object_id is not None:
                groups_repo.add_member(
                    group_id=group.id, catalog_object_id=object_id, sort_order=sort_order
                )
    return group.id
