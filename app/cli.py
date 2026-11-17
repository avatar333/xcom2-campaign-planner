"""Custom CLI commands (ARCHITECTURE.md §2, PRODUCT_SPEC FR-PLT-3).

`flask db upgrade`/`flask db migrate` come from Flask-Migrate itself
(ADR-0014). This module adds `flask seed-dev` (Phase 2) and the Phase 4
curated-content-pack ingestion commands (`flask content ...`, brief
"Database ingestion"): validate, import, refresh, diff, and a
development-only reset of catalog-only data.
"""
from __future__ import annotations

from pathlib import Path

import click
from flask import Flask

from app.services.seed import seed_dev

PACKS_ROOT = Path(__file__).resolve().parent.parent / "content" / "packs"


def _pack_dir_for(slug: str) -> Path:
    return PACKS_ROOT / slug


def _discover_pack_slugs() -> list[str]:
    """Discover and topologically sort pack directories by `requires`, so
    `flask content import-all` always imports a pack's dependencies first
    regardless of directory-name alphabetical order (e.g. `lwotc` requires
    `xcom2-base`/`wotc`, which must be imported before it)."""
    if not PACKS_ROOT.is_dir():
        return []

    import json as _json

    manifests: dict[str, dict] = {}
    for p in PACKS_ROOT.iterdir():
        manifest_path = p / "pack.json"
        if manifest_path.is_file():
            manifests[p.name] = _json.loads(manifest_path.read_text(encoding="utf-8"))

    ordered: list[str] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(slug: str) -> None:
        if slug in visited or slug not in manifests:
            return
        if slug in visiting:
            return  # defensive: a cycle in requires -- skip rather than infinite-loop
        visiting.add(slug)
        for dep_slug in manifests[slug].get("requires", []):
            visit(dep_slug)
        visiting.discard(slug)
        visited.add(slug)
        ordered.append(slug)

    for slug in sorted(manifests):
        visit(slug)
    return ordered


def register_cli(app: Flask) -> None:
    @app.cli.command("seed-dev")
    def seed_dev_command() -> None:
        """Seed foundational content sources and builtin catalog types/relationship types.

        Idempotent -- safe to run against an already-seeded database. Does
        NOT populate game objects (see DATABASE_SCHEMA.md §9).
        """
        counts = seed_dev()
        click.echo(
            "Seeded: "
            f"{counts['content_sources']} content source(s), "
            f"{counts['object_types']} object type(s), "
            f"{counts['relationship_types']} relationship type(s)."
        )

    content_group = app.cli.group("content")(lambda: None)
    content_group.help = "Curated content-pack management (docs/DATA_INGESTION.md)."

    @content_group.command("list")
    def content_list() -> None:
        """List curated pack directories found under content/packs/."""
        slugs = _discover_pack_slugs()
        if not slugs:
            click.echo(f"No pack directories found under {PACKS_ROOT}.")
            return
        for slug in slugs:
            click.echo(slug)

    @content_group.command("validate")
    @click.argument("slug")
    def content_validate(slug: str) -> None:
        """Dry-run validate a curated pack without writing to the database."""
        from app.importer.pack_schema import PackFormatError, load_pack
        from app.importer.pack_validator import validate_pack

        pack_dir = _pack_dir_for(slug)
        if not pack_dir.is_dir():
            raise click.ClickException(f"No such pack directory: {pack_dir}")
        try:
            document = load_pack(pack_dir)
        except PackFormatError as exc:
            raise click.ClickException(str(exc)) from exc

        report = validate_pack(document)
        click.echo(f"Validation report for '{slug}':")
        click.echo(f"  New objects: {report.new_objects}")
        click.echo(f"  Updated objects: {report.updated_objects}")
        click.echo(f"  Retired objects: {report.retired_objects}")
        click.echo(f"  New relationships: {report.new_relationships}")
        click.echo(f"  New prerequisite groups: {report.new_prerequisite_groups}")
        click.echo(f"  New stat profiles: {report.new_stat_profiles}")
        click.echo(f"  New asset manifest entries: {report.new_asset_manifest_entries}")
        if report.warnings:
            click.echo(f"  Warnings ({len(report.warnings)}):")
            for issue in report.warnings:
                click.echo(f"    - {issue.message}" + (f" [{issue.context}]" if issue.context else ""))
        if report.errors:
            click.echo(f"  Errors ({len(report.errors)}):")
            for issue in report.errors:
                click.echo(f"    - {issue.message}" + (f" [{issue.context}]" if issue.context else ""))
            raise click.ClickException(f"Pack '{slug}' failed validation ({len(report.errors)} error(s)).")
        click.echo("  Valid: pack can be imported.")

    @content_group.command("import")
    @click.argument("slug")
    @click.option("--force", is_flag=True, help="Re-import even if checksum is unchanged.")
    def content_import_command(slug: str, force: bool) -> None:
        """Import (or idempotently re-import) a curated content pack."""
        from app.services.content_import import ContentPackImportError, import_pack

        pack_dir = _pack_dir_for(slug)
        if not pack_dir.is_dir():
            raise click.ClickException(f"No such pack directory: {pack_dir}")
        try:
            result = import_pack(str(pack_dir), force=force)
        except ContentPackImportError as exc:
            raise click.ClickException(str(exc)) from exc

        if result.already_up_to_date:
            click.echo(f"'{slug}' is already up to date (checksum unchanged). Use --force to re-apply.")
            return

        click.echo(f"Imported '{slug}':")
        click.echo(f"  Object types created: {result.object_types_created}")
        click.echo(f"  Relationship types created: {result.relationship_types_created}")
        click.echo(f"  Objects created: {result.objects_created}")
        click.echo(f"  Objects updated: {result.objects_updated}")
        click.echo(f"  Objects retired: {result.objects_retired}")
        click.echo(f"  Relationships created: {result.relationships_created}")
        click.echo(f"  Prerequisite groups created: {result.prerequisite_groups_created}")
        click.echo(f"  Stat profiles created: {result.stat_profiles_created}")
        click.echo(f"  Asset manifest entries created: {result.asset_manifest_entries_created}")
        if result.errors:
            click.echo(f"  Warnings during import ({len(result.errors)}):")
            for message in result.errors:
                click.echo(f"    - {message}")

    @content_group.command("import-all")
    @click.option("--force", is_flag=True, help="Re-import even if checksum is unchanged.")
    def content_import_all(force: bool) -> None:
        """Import every pack under content/packs/ in dependency order,
        topologically sorted by each pack's `requires` declaration (see
        `_discover_pack_slugs`), so a pack that requires another is always
        imported after it regardless of directory name."""
        from app.services.content_import import ContentPackImportError, import_pack

        slugs = _discover_pack_slugs()
        if not slugs:
            click.echo(f"No pack directories found under {PACKS_ROOT}.")
            return
        for slug in slugs:
            try:
                result = import_pack(str(_pack_dir_for(slug)), force=force)
            except ContentPackImportError as exc:
                raise click.ClickException(f"'{slug}': {exc}") from exc
            status = "up to date" if result.already_up_to_date else "imported"
            click.echo(
                f"{slug}: {status} "
                f"(+{result.objects_created} objects, ~{result.objects_updated} updated, "
                f"{result.objects_retired} retired, +{result.relationships_created} relationships)"
            )

    @content_group.command("diff")
    @click.argument("slug")
    def content_diff(slug: str) -> None:
        """Show whether a pack's on-disk checksum differs from what was last imported."""
        from app.importer.pack_schema import PackFormatError, compute_pack_checksum
        from app.repositories import content_sources as sources_repo

        pack_dir = _pack_dir_for(slug)
        if not pack_dir.is_dir():
            raise click.ClickException(f"No such pack directory: {pack_dir}")
        try:
            current_checksum = compute_pack_checksum(pack_dir)
        except PackFormatError as exc:
            raise click.ClickException(str(exc)) from exc

        source = sources_repo.get_content_source_by_slug(slug)
        if source is None:
            click.echo(f"'{slug}' has never been imported. Run: flask content import {slug}")
            return
        if source.import_checksum == current_checksum:
            click.echo(f"'{slug}' is up to date (last imported {source.imported_at}).")
        else:
            click.echo(
                f"'{slug}' on-disk content differs from what was imported on {source.imported_at}. "
                f"Run: flask content import {slug}"
            )
        if source.source_repository:
            click.echo(
                f"  Pinned source: {source.source_repository} @ {source.source_ref} "
                f"({source.source_commit_sha})"
            )

    @content_group.command("reset-catalog")
    @click.option(
        "--yes", is_flag=True, required=True,
        help="Required confirmation flag -- this deletes ALL built-in catalog data.",
    )
    def content_reset_catalog(yes: bool) -> None:
        """Development-only: delete all built-in (non-user-authored) catalog
        data so curated packs can be re-imported from a clean slate.

        Never touches `campaign_*` tables or user-authored
        (`is_user_authored=True`) objects/sources -- see
        `docs/DATA_INGESTION.md` "catalog-only reset" for the exact scope.
        """
        if not yes:
            raise click.ClickException("Refusing to reset without --yes.")

        from app.extensions import db
        from app.models import (
            CatalogObject,
            CatalogPrerequisiteGroup,
            CatalogRelationship,
            CatalogSoldierStatProfile,
            ContentSource,
        )

        builtin_source_ids = [
            s.id for s in ContentSource.query.filter_by(is_builtin=True).all()
        ]
        if not builtin_source_ids:
            click.echo("No built-in content sources found; nothing to reset.")
            return

        CatalogSoldierStatProfile.query.filter(
            CatalogSoldierStatProfile.source_pack_id.in_(builtin_source_ids)
        ).delete(synchronize_session=False)
        CatalogPrerequisiteGroup.query.filter(
            CatalogPrerequisiteGroup.source_pack_id.in_(builtin_source_ids)
        ).delete(synchronize_session=False)
        CatalogRelationship.query.filter(
            CatalogRelationship.source_pack_id.in_(builtin_source_ids)
        ).delete(synchronize_session=False)
        objects_deleted = CatalogObject.query.filter(
            CatalogObject.source_id.in_(builtin_source_ids), CatalogObject.is_user_authored.is_(False)
        ).delete(synchronize_session=False)
        for source in ContentSource.query.filter_by(is_builtin=True).all():
            source.import_checksum = None
            source.imported_at = None
        db.session.commit()
        click.echo(f"Reset catalog data for {len(builtin_source_ids)} built-in source(s); {objects_deleted} object(s) removed.")
