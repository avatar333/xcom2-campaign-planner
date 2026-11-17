"""End-to-end tests for the curated content-pack importer
(`app/services/content_import.py`, Phase 4 brief "Database ingestion").

Distinct from Phase 3's `test_catalog_import_export.py` (the ad hoc UI
interchange format, `ADR-0017`) -- these exercise the `content/packs/`
directory importer that writes curated XCOM/LWotC facts."""
from __future__ import annotations

import json

import pytest

from tests.helpers import make_png_bytes


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


def test_import_creates_source_and_objects(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path,
            manifest={"slug": "newpack", "name": "New Pack", "kind": "base", "version": "1.0.0"},
            objects=[
                {"template_key": "Thing", "type": "weapon", "name": "Thing", "tier": 1, "tags": ["primary"]},
            ],
        )
        result = import_pack(str(pack_dir))

        assert result.objects_created == 1
        assert result.objects_updated == 0
        assert not result.already_up_to_date

        from app.models import CatalogObject, ContentSource

        source = ContentSource.query.filter_by(slug="newpack").first()
        assert source is not None
        assert source.import_checksum == result.checksum

        obj = CatalogObject.query.filter_by(source_id=source.id, template_key="Thing").first()
        assert obj is not None
        assert obj.name == "Thing"
        assert obj.tier == 1


def test_reimport_unchanged_pack_is_noop(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(tmp_path, objects=[{"template_key": "Thing", "type": "weapon", "name": "Thing"}])
        first = import_pack(str(pack_dir))
        second = import_pack(str(pack_dir))

        assert not first.already_up_to_date
        assert second.already_up_to_date
        assert second.objects_created == 0

        from app.models import CatalogObject

        assert CatalogObject.query.filter_by(template_key="Thing").count() == 1


def test_reimport_changed_pack_updates_in_place_without_duplicating(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path, objects=[{"template_key": "Thing", "type": "weapon", "name": "Original Name"}]
        )
        import_pack(str(pack_dir))

        (pack_dir / "objects.json").write_text(
            json.dumps([{"template_key": "Thing", "type": "weapon", "name": "New Name", "tier": 3}]),
            encoding="utf-8",
        )
        result = import_pack(str(pack_dir))

        assert result.objects_updated == 1
        assert result.objects_created == 0

        from app.models import CatalogObject

        objs = CatalogObject.query.filter_by(template_key="Thing").all()
        assert len(objs) == 1
        assert objs[0].name == "New Name"
        assert objs[0].tier == 3


def test_force_reimports_even_when_unchanged(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(tmp_path, objects=[{"template_key": "Thing", "type": "weapon", "name": "Thing"}])
        import_pack(str(pack_dir))
        result = import_pack(str(pack_dir), force=True)

        assert not result.already_up_to_date


def test_object_removed_from_newer_version_is_retired_not_deleted(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[
                {"template_key": "Keeper", "type": "weapon", "name": "Keeper"},
                {"template_key": "Goner", "type": "weapon", "name": "Goner"},
            ],
        )
        import_pack(str(pack_dir))

        (pack_dir / "objects.json").write_text(
            json.dumps([{"template_key": "Keeper", "type": "weapon", "name": "Keeper"}]),
            encoding="utf-8",
        )
        result = import_pack(str(pack_dir))

        assert result.objects_retired == 1

        from app.models import CatalogObject

        goner = CatalogObject.query.filter_by(template_key="Goner").first()
        assert goner is not None  # never deleted
        assert goner.is_retired is True


def test_object_removed_but_referenced_by_campaign_is_orphaned_not_retired(app, seeded_db, tmp_path):
    with app.app_context():
        from app.extensions import db
        from app.models import CampaignObjectState, CatalogObject
        from app.services import campaign_lifecycle
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path, objects=[{"template_key": "Referenced", "type": "weapon", "name": "Referenced"}]
        )
        import_pack(str(pack_dir))

        referenced = CatalogObject.query.filter_by(template_key="Referenced").first()
        campaign = campaign_lifecycle.create_campaign(name="Campaign", game_profile_slug=None)
        db.session.add(
            CampaignObjectState(campaign_id=campaign.id, catalog_object_id=referenced.id, status="owned")
        )
        db.session.commit()

        (pack_dir / "objects.json").write_text(json.dumps([]), encoding="utf-8")
        result = import_pack(str(pack_dir))

        assert result.objects_retired == 1

        reloaded = CatalogObject.query.filter_by(template_key="Referenced").first()
        assert reloaded.is_retired is False
        assert reloaded.is_orphaned_upstream is True


def test_import_creates_relationships(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[
                {"template_key": "A", "type": "research_project", "name": "A"},
                {"template_key": "B", "type": "research_project", "name": "B"},
            ],
            extra_files={
                "relationships.json": [
                    {"type": "prerequisite", "from": ["pack", "A"], "to": ["pack", "B"]}
                ]
            },
        )
        result = import_pack(str(pack_dir))

        assert result.relationships_created == 1

        from app.models import CatalogObject, CatalogRelationship

        a = CatalogObject.query.filter_by(template_key="A").first()
        b = CatalogObject.query.filter_by(template_key="B").first()
        rel = CatalogRelationship.query.filter_by(source_object_id=a.id, target_object_id=b.id).first()
        assert rel is not None


def test_reimporting_relationships_does_not_duplicate(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[
                {"template_key": "A", "type": "research_project", "name": "A"},
                {"template_key": "B", "type": "research_project", "name": "B"},
            ],
            extra_files={
                "relationships.json": [
                    {"type": "prerequisite", "from": ["pack", "A"], "to": ["pack", "B"]}
                ]
            },
        )
        import_pack(str(pack_dir))
        second = import_pack(str(pack_dir), force=True)

        assert second.relationships_created == 0

        from app.models import CatalogRelationship

        assert CatalogRelationship.query.count() == 1


def test_import_creates_new_object_type_and_attribute_definitions(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[
                {
                    "template_key": "Thing",
                    "type": "avenger_room_state",
                    "name": "Thing",
                    "attributes": {"custom_key": "value"},
                }
            ],
            extra_files={
                "object_types.json": [
                    {
                        "slug": "avenger_room_state",
                        "name": "Avenger Room State",
                        "attributes": [{"key": "custom_key", "data_type": "string"}],
                    }
                ]
            },
        )
        result = import_pack(str(pack_dir))

        assert result.object_types_created == 1

        from app.repositories import object_types as object_types_repo

        object_type = object_types_repo.get_object_type_by_slug("avenger_room_state")
        assert object_type is not None
        defs = object_types_repo.list_attribute_definitions(object_type.id)
        assert any(d.key == "custom_key" for d in defs)


def test_import_creates_compound_prerequisite_group(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[
                {"template_key": "LaserPath", "type": "research_project", "name": "LaserPath"},
                {"template_key": "CoilPath", "type": "research_project", "name": "CoilPath"},
                {"template_key": "Plasma", "type": "research_project", "name": "Plasma"},
            ],
            extra_files={
                "prerequisite_groups.json": [
                    {
                        "target": ["pack", "Plasma"],
                        "relationship_type": "prerequisite",
                        "root": {
                            "logic": "OR",
                            "members": [["pack", "LaserPath"], ["pack", "CoilPath"]],
                        },
                    }
                ]
            },
        )
        result = import_pack(str(pack_dir))

        assert result.prerequisite_groups_created == 1

        from app.models import CatalogObject
        from app.repositories import prerequisite_groups as groups_repo

        plasma = CatalogObject.query.filter_by(template_key="Plasma").first()
        roots = groups_repo.list_root_groups_for_object(plasma.id)
        assert len(roots) == 1
        assert roots[0].logic == "OR"
        members = groups_repo.list_members(roots[0].id)
        assert len(members) == 2


def test_import_creates_soldier_stat_profile(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[{"template_key": "Rookie", "type": "soldier_class", "name": "Rookie"}],
            extra_files={
                "stat_profiles.json": [
                    {
                        "target": ["pack", "Rookie"],
                        "stat_key": "health",
                        "difficulty": "veteran",
                        "stat_default": 5,
                    }
                ]
            },
        )
        result = import_pack(str(pack_dir))

        assert result.stat_profiles_created == 1

        from app.models import CatalogObject
        from app.repositories import soldier_stat_profiles as stat_profiles_repo

        rookie = CatalogObject.query.filter_by(template_key="Rookie").first()
        profiles = stat_profiles_repo.list_for_object(rookie.id)
        assert len(profiles) == 1
        assert profiles[0].stat_default == 5


def test_import_records_pending_asset_manifest_entry_without_local_file(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[{"template_key": "Thing", "type": "weapon", "name": "Thing"}],
            extra_files={
                "asset_manifest.json": [
                    {
                        "target": ["pack", "Thing"],
                        "role": "icon",
                        "source_name": "UFOpaedia",
                        "source_url": "https://ufopaedia.org/thing",
                    }
                ]
            },
        )
        result = import_pack(str(pack_dir))

        assert result.asset_manifest_entries_created == 1

        from app.models import Asset

        asset = Asset.query.filter_by(source_name="UFOpaedia").first()
        assert asset is not None
        assert asset.import_status == "pending"


def test_import_bundles_local_asset_file(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[{"template_key": "Thing", "type": "weapon", "name": "Thing"}],
            extra_files={
                "asset_manifest.json": [
                    {"target": ["pack", "Thing"], "role": "icon", "local_path": "assets/thing.png"}
                ]
            },
        )
        assets_dir = pack_dir / "assets"
        assets_dir.mkdir()
        (assets_dir / "thing.png").write_bytes(make_png_bytes())

        result = import_pack(str(pack_dir))

        assert result.asset_manifest_entries_created == 1
        assert not result.errors

        from app.models import Asset, CatalogObject

        thing = CatalogObject.query.filter_by(template_key="Thing").first()
        from app.extensions import db

        reloaded = db.session.get(CatalogObject, thing.id)
        assert reloaded.primary_asset_id is not None
        asset = db.session.get(Asset, reloaded.primary_asset_id)
        assert asset.import_status == "imported"


def test_import_missing_local_asset_file_records_error_but_does_not_fail_import(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path,
            objects=[{"template_key": "Thing", "type": "weapon", "name": "Thing"}],
            extra_files={
                "asset_manifest.json": [
                    {"target": ["pack", "Thing"], "role": "icon", "local_path": "assets/missing.png"}
                ]
            },
        )
        result = import_pack(str(pack_dir))

        assert any("missing local file" in e for e in result.errors)


def test_import_overrides_object_from_required_source(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import import_pack

        base_pack_dir = _write_pack(
            tmp_path,
            name="base",
            manifest={"slug": "base", "name": "Base", "kind": "base", "version": "1.0.0"},
            objects=[{"template_key": "Rifle", "type": "weapon", "name": "Base Rifle"}],
        )
        import_pack(str(base_pack_dir))

        overlay_pack_dir = _write_pack(
            tmp_path,
            name="overlay",
            manifest={
                "slug": "overlay", "name": "Overlay", "kind": "total_conversion", "version": "1.0.0",
                "requires": ["base"], "extends": ["base"],
            },
            objects=[
                {
                    "template_key": "Rifle_Overlay",
                    "type": "weapon",
                    "name": "Overlay Rifle",
                    "overrides": ["base", "Rifle"],
                    "patch": {"attributes": {"damage_min": 99}},
                }
            ],
        )
        result = import_pack(str(overlay_pack_dir))

        assert result.objects_created == 1

        from app.models import CatalogObject

        base_obj = CatalogObject.query.filter_by(template_key="Rifle").first()
        overlay_obj = CatalogObject.query.filter_by(template_key="Rifle_Overlay").first()
        assert overlay_obj.overrides_object_id == base_obj.id
        assert json.loads(overlay_obj.patch) == {"attributes": {"damage_min": 99}}


def test_import_raises_on_validation_error(app, seeded_db, tmp_path):
    with app.app_context():
        from app.services.content_import import ContentPackImportError, import_pack

        pack_dir = _write_pack(
            tmp_path, objects=[{"template_key": "Thing", "type": "not_a_real_type", "name": "Thing"}]
        )
        with pytest.raises(ContentPackImportError):
            import_pack(str(pack_dir))


def test_import_never_touches_user_authored_objects_in_same_source_slug(app, seeded_db, tmp_path):
    """A pack only ever upserts rows under its OWN source_id; even if a
    user-authored object happens to share a template_key coincidentally in
    a different source, it must remain untouched."""
    with app.app_context():
        from app.services import catalog_objects as objects_service
        from app.services.content_import import import_pack
        from app.services.content_sources import get_or_create_user_source

        user_source = get_or_create_user_source()
        user_obj = objects_service.create_object(
            source_id=user_source.id,
            type_id=_weapon_type_id(),
            name="My Custom Thing",
            template_key="Thing",
            is_user_authored=True,
        )

        pack_dir = _write_pack(tmp_path, objects=[{"template_key": "Thing", "type": "weapon", "name": "Pack Thing"}])
        import_pack(str(pack_dir))

        from app.extensions import db
        from app.models import CatalogObject

        reloaded_user_obj = db.session.get(CatalogObject, user_obj.id)
        assert reloaded_user_obj.name == "My Custom Thing"
        assert reloaded_user_obj.is_user_authored is True


def test_import_bumps_effective_catalog_version_for_affected_campaigns(app, seeded_db, tmp_path):
    """Re-importing a changed pack must invalidate the effective-catalog
    cache for every campaign that has the pack's source enabled
    (`ARCHITECTURE.md` §7.3) -- otherwise a campaign created before the
    re-import would keep seeing stale resolved objects."""
    with app.app_context():
        from app.repositories.content_sources import get_content_source_by_slug
        from app.services import campaign_lifecycle
        from app.services import campaign_sources as campaign_sources_service
        from app.services import effective_catalog as effective_catalog_service
        from app.services.content_import import import_pack

        pack_dir = _write_pack(
            tmp_path,
            manifest={"slug": "versioned", "name": "Versioned", "kind": "base", "version": "1.0.0"},
            objects=[{"template_key": "Thing", "type": "weapon", "name": "Original Name"}],
        )
        import_pack(str(pack_dir))

        source = get_content_source_by_slug("versioned")
        campaign = campaign_lifecycle.create_campaign(name="Affected Campaign", game_profile_slug=None)
        campaign_sources_service.add_source_to_campaign(campaign, source)

        version_before = campaign.effective_catalog_version
        effective_before = effective_catalog_service.get_effective_catalog(
            campaign.id, version=version_before
        )
        thing_id = next(iter(effective_before))
        assert effective_before[thing_id]["name"] == "Original Name"

        (pack_dir / "objects.json").write_text(
            json.dumps([{"template_key": "Thing", "type": "weapon", "name": "Renamed"}]), encoding="utf-8"
        )
        import_pack(str(pack_dir))

        from app.extensions import db

        db.session.refresh(campaign)
        version_after = campaign.effective_catalog_version
        assert version_after != version_before

        effective_after = effective_catalog_service.get_effective_catalog(campaign.id, version=version_after)
        assert effective_after[thing_id]["name"] == "Renamed"


def _weapon_type_id() -> int:
    from app.models import CatalogObjectType

    return CatalogObjectType.query.filter_by(slug="weapon").first().id
