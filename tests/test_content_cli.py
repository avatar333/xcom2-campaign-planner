"""Tests for the `flask content *` CLI commands
(`app/cli.py`, Phase 4 brief "Database ingestion")."""
from __future__ import annotations

import json


def _write_pack(packs_root, *, manifest=None, objects=None, extra_files=None, name="pack"):
    pack_dir = packs_root / name
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.json").write_text(
        json.dumps(
            manifest or {"slug": name, "name": name, "kind": "user", "version": "1.0.0"}
        ),
        encoding="utf-8",
    )
    (pack_dir / "objects.json").write_text(
        json.dumps(objects if objects is not None else [{"template_key": "T", "type": "weapon", "name": "T"}]),
        encoding="utf-8",
    )
    for filename, content in (extra_files or {}).items():
        (pack_dir / filename).write_text(json.dumps(content), encoding="utf-8")
    return pack_dir


def _patch_packs_root(monkeypatch, packs_root):
    import app.cli as cli_module

    monkeypatch.setattr(cli_module, "PACKS_ROOT", packs_root)


def test_content_list_shows_discovered_packs(app, seeded_db, tmp_path, monkeypatch):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    _write_pack(packs_root, name="alpha")
    _patch_packs_root(monkeypatch, packs_root)

    runner = app.test_cli_runner()
    result = runner.invoke(args=["content", "list"])

    assert result.exit_code == 0
    assert "alpha" in result.output


def test_content_list_orders_dependencies_before_dependents(app, seeded_db, tmp_path, monkeypatch):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    _write_pack(packs_root, name="base", manifest={"slug": "base", "name": "Base", "kind": "base", "version": "1.0.0"})
    _write_pack(
        packs_root,
        name="zextension",
        manifest={
            "slug": "zextension", "name": "Z", "kind": "user", "version": "1.0.0",
            "requires": ["base"],
        },
    )
    _patch_packs_root(monkeypatch, packs_root)

    runner = app.test_cli_runner()
    result = runner.invoke(args=["content", "list"])

    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines.index("base") < lines.index("zextension")


def test_content_validate_reports_success(app, seeded_db, tmp_path, monkeypatch):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    _write_pack(packs_root, name="alpha")
    _patch_packs_root(monkeypatch, packs_root)

    runner = app.test_cli_runner()
    result = runner.invoke(args=["content", "validate", "alpha"])

    assert result.exit_code == 0
    assert "Valid: pack can be imported." in result.output


def test_content_validate_fails_on_invalid_pack(app, seeded_db, tmp_path, monkeypatch):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    _write_pack(
        packs_root, name="bad", objects=[{"template_key": "T", "type": "not_a_real_type", "name": "T"}]
    )
    _patch_packs_root(monkeypatch, packs_root)

    runner = app.test_cli_runner()
    result = runner.invoke(args=["content", "validate", "bad"])

    assert result.exit_code != 0


def test_content_validate_missing_pack_dir_errors(app, seeded_db, tmp_path, monkeypatch):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    _patch_packs_root(monkeypatch, packs_root)

    runner = app.test_cli_runner()
    result = runner.invoke(args=["content", "validate", "does-not-exist"])

    assert result.exit_code != 0


def test_content_import_writes_objects(app, seeded_db, tmp_path, monkeypatch):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    _write_pack(packs_root, name="alpha")
    _patch_packs_root(monkeypatch, packs_root)

    runner = app.test_cli_runner()
    result = runner.invoke(args=["content", "import", "alpha"])

    assert result.exit_code == 0
    assert "Objects created: 1" in result.output

    with app.app_context():
        from app.models import CatalogObject

        assert CatalogObject.query.filter_by(template_key="T").count() == 1


def test_content_import_reports_up_to_date_on_second_run(app, seeded_db, tmp_path, monkeypatch):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    _write_pack(packs_root, name="alpha")
    _patch_packs_root(monkeypatch, packs_root)

    runner = app.test_cli_runner()
    runner.invoke(args=["content", "import", "alpha"])
    result = runner.invoke(args=["content", "import", "alpha"])

    assert "already up to date" in result.output


def test_content_import_all_imports_in_dependency_order(app, seeded_db, tmp_path, monkeypatch):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    _write_pack(packs_root, name="base", manifest={"slug": "base", "name": "Base", "kind": "base", "version": "1.0.0"})
    _write_pack(
        packs_root,
        name="zextension",
        manifest={
            "slug": "zextension", "name": "Z", "kind": "user", "version": "1.0.0",
            "requires": ["base"],
        },
    )
    _patch_packs_root(monkeypatch, packs_root)

    runner = app.test_cli_runner()
    result = runner.invoke(args=["content", "import-all"])

    assert result.exit_code == 0
    assert "base: imported" in result.output
    assert "zextension: imported" in result.output


def test_content_diff_reports_never_imported(app, seeded_db, tmp_path, monkeypatch):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    _write_pack(packs_root, name="alpha")
    _patch_packs_root(monkeypatch, packs_root)

    runner = app.test_cli_runner()
    result = runner.invoke(args=["content", "diff", "alpha"])

    assert "never been imported" in result.output


def test_content_diff_reports_up_to_date_after_import(app, seeded_db, tmp_path, monkeypatch):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    _write_pack(packs_root, name="alpha")
    _patch_packs_root(monkeypatch, packs_root)

    runner = app.test_cli_runner()
    runner.invoke(args=["content", "import", "alpha"])
    result = runner.invoke(args=["content", "diff", "alpha"])

    assert "is up to date" in result.output


def test_content_reset_catalog_requires_yes_flag(app, seeded_db):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["content", "reset-catalog"])

    assert result.exit_code != 0


def test_content_reset_catalog_removes_builtin_but_not_user_objects(app, seeded_db, tmp_path, monkeypatch):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    _write_pack(packs_root, name="alpha", manifest={"slug": "alpha", "name": "Alpha", "kind": "base", "version": "1.0.0"})
    _patch_packs_root(monkeypatch, packs_root)

    runner = app.test_cli_runner()
    runner.invoke(args=["content", "import", "alpha"])

    with app.app_context():
        from app.models import CatalogObjectType
        from app.services import catalog_objects as objects_service
        from app.services.content_sources import get_or_create_user_source

        user_source = get_or_create_user_source()
        weapon_type_id = CatalogObjectType.query.filter_by(slug="weapon").first().id
        objects_service.create_object(
            source_id=user_source.id, type_id=weapon_type_id, name="My Thing", is_user_authored=True
        )

    result = runner.invoke(args=["content", "reset-catalog", "--yes"])
    assert result.exit_code == 0

    with app.app_context():
        from app.models import CatalogObject

        assert CatalogObject.query.filter_by(template_key="T").count() == 0
        assert CatalogObject.query.filter_by(name="My Thing").count() == 1
