"""Tests for the curated content-pack loader (`app/importer/pack_schema.py`,
Phase 4 brief "Primary objective" + ARCHITECTURE.md §5)."""
from __future__ import annotations

import json

import pytest

from app.importer.pack_schema import PackFormatError, compute_pack_checksum, load_pack


def _write_pack(tmp_path, *, manifest=None, objects=None, extra_files=None):
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "pack.json").write_text(
        json.dumps(
            manifest
            or {"slug": "test-pack", "name": "Test Pack", "kind": "user", "version": "1.0.0"}
        ),
        encoding="utf-8",
    )
    (pack_dir / "objects.json").write_text(
        json.dumps(
            objects
            if objects is not None
            else [{"template_key": "Thing", "type": "weapon", "name": "Thing"}]
        ),
        encoding="utf-8",
    )
    for filename, content in (extra_files or {}).items():
        (pack_dir / filename).write_text(json.dumps(content), encoding="utf-8")
    return pack_dir


def test_load_minimal_pack(tmp_path):
    pack_dir = _write_pack(tmp_path)
    document = load_pack(pack_dir)

    assert document.manifest.slug == "test-pack"
    assert len(document.objects) == 1
    assert document.objects[0].template_key == "Thing"
    assert document.objects[0].type_slug == "weapon"


def test_load_pack_missing_manifest_raises(tmp_path):
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    with pytest.raises(PackFormatError):
        load_pack(pack_dir)


def test_load_pack_missing_objects_json_raises(tmp_path):
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "pack.json").write_text(
        json.dumps({"slug": "s", "name": "S", "kind": "user", "version": "1.0.0"}), encoding="utf-8"
    )
    with pytest.raises(PackFormatError):
        load_pack(pack_dir)


def test_load_pack_manifest_missing_required_key_raises(tmp_path):
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "pack.json").write_text(json.dumps({"slug": "s", "name": "S"}), encoding="utf-8")
    (pack_dir / "objects.json").write_text("[]", encoding="utf-8")
    with pytest.raises(PackFormatError):
        load_pack(pack_dir)


def test_load_pack_future_schema_version_raises(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        manifest={
            "slug": "s", "name": "S", "kind": "user", "version": "1.0.0", "schema_version": 999,
        },
    )
    with pytest.raises(PackFormatError):
        load_pack(pack_dir)


def test_load_pack_object_missing_required_key_raises(tmp_path):
    pack_dir = _write_pack(tmp_path, objects=[{"template_key": "Thing", "name": "Thing"}])
    with pytest.raises(PackFormatError):
        load_pack(pack_dir)


def test_load_pack_with_overrides_and_patch(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        objects=[
            {
                "template_key": "OverriddenThing",
                "type": "weapon",
                "name": "Overridden Thing",
                "overrides": ["xcom2-base", "BaseThing"],
                "patch": {"attributes": {"damage_min": 10}},
            }
        ],
    )
    document = load_pack(pack_dir)

    assert document.objects[0].overrides == ("xcom2-base", "BaseThing")
    assert document.objects[0].patch == {"attributes": {"damage_min": 10}}


def test_load_pack_relationships(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        extra_files={
            "relationships.json": [
                {
                    "type": "prerequisite",
                    "from": ["test-pack", "A"],
                    "to": ["test-pack", "Thing"],
                    "group_key": "grp",
                    "group_logic": "OR",
                }
            ]
        },
    )
    document = load_pack(pack_dir)

    assert len(document.relationships) == 1
    rel = document.relationships[0]
    assert rel.type_slug == "prerequisite"
    assert rel.source == ("test-pack", "A")
    assert rel.target == ("test-pack", "Thing")
    assert rel.group_logic == "OR"


def test_load_pack_relationship_missing_key_raises(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        extra_files={"relationships.json": [{"type": "prerequisite", "from": ["a", "b"]}]},
    )
    with pytest.raises(PackFormatError):
        load_pack(pack_dir)


def test_load_pack_bad_pair_shape_raises(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        extra_files={
            "relationships.json": [{"type": "prerequisite", "from": "not-a-pair", "to": ["a", "b"]}]
        },
    )
    with pytest.raises(PackFormatError):
        load_pack(pack_dir)


def test_load_pack_nested_prerequisite_group(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        extra_files={
            "prerequisite_groups.json": [
                {
                    "target": ["test-pack", "Thing"],
                    "relationship_type": "prerequisite",
                    "root": {
                        "logic": "OR",
                        "members": [
                            ["test-pack", "A"],
                            {
                                "logic": "AND",
                                "members": [["test-pack", "B"], ["test-pack", "C"]],
                            },
                        ],
                    },
                }
            ]
        },
    )
    document = load_pack(pack_dir)

    group = document.prerequisite_groups[0]
    assert group.target == ("test-pack", "Thing")
    assert group.root.logic == "OR"
    assert group.root.members[0] == ("test-pack", "A")
    nested = group.root.members[1]
    assert nested.logic == "AND"
    assert nested.members == [("test-pack", "B"), ("test-pack", "C")]


def test_load_pack_bad_group_logic_raises(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        extra_files={
            "prerequisite_groups.json": [
                {
                    "target": ["test-pack", "Thing"],
                    "relationship_type": "prerequisite",
                    "root": {"logic": "XOR", "members": [["test-pack", "A"]]},
                }
            ]
        },
    )
    with pytest.raises(PackFormatError):
        load_pack(pack_dir)


def test_load_pack_stat_profiles_and_asset_manifest(tmp_path):
    pack_dir = _write_pack(
        tmp_path,
        extra_files={
            "stat_profiles.json": [
                {
                    "target": ["test-pack", "Thing"],
                    "stat_key": "hp",
                    "difficulty": "veteran",
                    "stat_default": 5,
                }
            ],
            "asset_manifest.json": [
                {"target": ["test-pack", "Thing"], "role": "icon", "source_name": "UFOpaedia"}
            ],
        },
    )
    document = load_pack(pack_dir)

    assert document.stat_profiles[0].stat_key == "hp"
    assert document.stat_profiles[0].difficulty == "veteran"
    assert document.asset_manifest[0].role == "icon"
    assert document.asset_manifest[0].import_status == "pending"


def test_compute_pack_checksum_stable_and_sensitive_to_content(tmp_path):
    pack_dir = _write_pack(tmp_path)
    checksum_1 = compute_pack_checksum(pack_dir)
    checksum_2 = compute_pack_checksum(pack_dir)
    assert checksum_1 == checksum_2

    (pack_dir / "objects.json").write_text(
        json.dumps([{"template_key": "Thing", "type": "weapon", "name": "Changed Name"}]),
        encoding="utf-8",
    )
    checksum_3 = compute_pack_checksum(pack_dir)
    assert checksum_3 != checksum_1
