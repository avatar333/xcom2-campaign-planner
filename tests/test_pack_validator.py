"""Tests for curated content-pack dry-run validation
(`app/importer/pack_validator.py`, Phase 4 brief "Validation")."""
from __future__ import annotations

import json

from app.importer.pack_schema import load_pack


def _write_pack(tmp_path, *, manifest=None, objects=None, extra_files=None, name="pack"):
    pack_dir = tmp_path / name
    pack_dir.mkdir()
    (pack_dir / "pack.json").write_text(
        json.dumps(
            manifest or {"slug": name, "name": name, "kind": "user", "version": "1.0.0"}
        ),
        encoding="utf-8",
    )
    (pack_dir / "objects.json").write_text(
        json.dumps(objects if objects is not None else []), encoding="utf-8"
    )
    for filename, content in (extra_files or {}).items():
        (pack_dir / filename).write_text(json.dumps(content), encoding="utf-8")
    return pack_dir


def test_valid_minimal_pack_has_no_errors(app, seeded_db, tmp_path):
    with app.app_context():
        from app.importer.pack_validator import validate_pack

        pack_dir = _write_pack(
            tmp_path,
            manifest={"slug": "s1", "name": "S1", "kind": "user", "version": "1.0.0"},
            objects=[{"template_key": "Thing", "type": "weapon", "name": "Thing"}],
        )
        document = load_pack(pack_dir)
        report = validate_pack(document)

        assert report.is_valid
        assert report.new_objects == 1


def test_object_with_unknown_type_is_an_error(app, seeded_db, tmp_path):
    with app.app_context():
        from app.importer.pack_validator import validate_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[{"template_key": "Thing", "type": "not_a_real_type", "name": "Thing"}],
        )
        document = load_pack(pack_dir)
        report = validate_pack(document)

        assert not report.is_valid
        assert any("unknown object type" in i.message for i in report.errors)


def test_requires_missing_source_is_an_error(app, seeded_db, tmp_path):
    with app.app_context():
        from app.importer.pack_validator import validate_pack

        pack_dir = _write_pack(
            tmp_path,
            manifest={
                "slug": "needs-other", "name": "N", "kind": "user", "version": "1.0.0",
                "requires": ["does-not-exist"],
            },
        )
        document = load_pack(pack_dir)
        report = validate_pack(document)

        assert not report.is_valid
        assert any("has not been imported" in i.message for i in report.errors)


def test_conflicts_with_imported_source_is_a_warning(app, seeded_db, tmp_path):
    with app.app_context():
        from app.importer.pack_validator import validate_pack
        from app.services.content_sources import create_content_source

        create_content_source(slug="rival", name="Rival", kind="workshop_mod")

        pack_dir = _write_pack(
            tmp_path,
            manifest={
                "slug": "conflicting", "name": "C", "kind": "user", "version": "1.0.0",
                "conflicts": ["rival"],
            },
        )
        document = load_pack(pack_dir)
        report = validate_pack(document)

        assert report.is_valid  # conflicts are warnings, not errors
        assert any("conflicts with" in i.message for i in report.warnings)


def test_duplicate_object_identity_within_pack_is_an_error(app, seeded_db, tmp_path):
    with app.app_context():
        from app.importer.pack_validator import validate_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[
                {"template_key": "Dup", "type": "weapon", "name": "First"},
                {"template_key": "Dup", "type": "weapon", "name": "Second"},
            ],
        )
        document = load_pack(pack_dir)
        report = validate_pack(document)

        assert not report.is_valid
        assert any("Duplicate object identity" in i.message for i in report.errors)


def test_self_referencing_relationship_is_an_error(app, seeded_db, tmp_path):
    with app.app_context():
        from app.importer.pack_validator import validate_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[{"template_key": "Thing", "type": "weapon", "name": "Thing"}],
            extra_files={
                "relationships.json": [
                    {"type": "prerequisite", "from": ["pack", "Thing"], "to": ["pack", "Thing"]}
                ]
            },
        )
        document = load_pack(pack_dir)
        report = validate_pack(document)

        assert not report.is_valid
        assert any("connect an object to itself" in i.message for i in report.errors)


def test_unresolved_relationship_endpoint_is_an_error(app, seeded_db, tmp_path):
    with app.app_context():
        from app.importer.pack_validator import validate_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[{"template_key": "Thing", "type": "weapon", "name": "Thing"}],
            extra_files={
                "relationships.json": [
                    {
                        "type": "prerequisite",
                        "from": ["pack", "DoesNotExist"],
                        "to": ["pack", "Thing"],
                    }
                ]
            },
        )
        document = load_pack(pack_dir)
        report = validate_pack(document)

        assert not report.is_valid
        assert any("not found" in i.message for i in report.errors)


def test_override_outside_declared_sources_is_an_error(app, seeded_db, tmp_path):
    with app.app_context():
        from app.importer.pack_validator import validate_pack
        from app.services.content_sources import create_content_source

        create_content_source(slug="other-base", name="Other Base", kind="base")

        pack_dir = _write_pack(
            tmp_path,
            objects=[
                {
                    "template_key": "Thing",
                    "type": "weapon",
                    "name": "Thing",
                    "overrides": ["other-base", "BaseThing"],
                }
            ],
        )
        document = load_pack(pack_dir)
        report = validate_pack(document)

        assert not report.is_valid
        assert any("does not declare in requires/extends" in i.message for i in report.errors)


def test_empty_prerequisite_group_is_an_error(app, seeded_db, tmp_path):
    with app.app_context():
        from app.importer.pack_validator import validate_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[{"template_key": "Thing", "type": "weapon", "name": "Thing"}],
            extra_files={
                "prerequisite_groups.json": [
                    {
                        "target": ["pack", "Thing"],
                        "relationship_type": "prerequisite",
                        "root": {"logic": "OR", "members": []},
                    }
                ]
            },
        )
        document = load_pack(pack_dir)
        report = validate_pack(document)

        assert not report.is_valid
        assert any("no members" in i.message for i in report.errors)


def test_valid_pack_against_already_imported_source_resolves_cross_source_refs(app, seeded_db, tmp_path):
    with app.app_context():
        from app.importer.pack_validator import validate_pack
        from app.services import catalog_objects as objects_service
        from app.services.content_sources import create_content_source

        base = create_content_source(slug="imported-base", name="Imported Base", kind="base")
        objects_service.create_object(source_id=base.id, type_id=_weapon_type_id(), name="Base Thing")
        from app.models import CatalogObject

        base_obj = CatalogObject.query.filter_by(name="Base Thing").first()
        base_obj.template_key = "BaseThing"
        from app.extensions import db

        db.session.commit()

        pack_dir = _write_pack(
            tmp_path,
            manifest={
                "slug": "extension", "name": "Extension", "kind": "user", "version": "1.0.0",
                "requires": ["imported-base"],
            },
            objects=[{"template_key": "Thing", "type": "weapon", "name": "Thing"}],
            extra_files={
                "relationships.json": [
                    {
                        "type": "prerequisite",
                        "from": ["imported-base", "BaseThing"],
                        "to": ["extension", "Thing"],
                    }
                ]
            },
        )
        document = load_pack(pack_dir)
        report = validate_pack(document)

        assert report.is_valid


def _weapon_type_id() -> int:
    from app.models import CatalogObjectType

    return CatalogObjectType.query.filter_by(slug="weapon").first().id
