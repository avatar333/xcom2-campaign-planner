"""Equipment-build service tests (ADR-0022): CRUD, slots, compatibility
validation and manual override, mod/custom catalog objects, reuse,
unavailable/planned equipment, campaign isolation, safe deletion."""
from __future__ import annotations

import pytest

from tests.avenger_helpers import (
    attach_source,
    import_real_packs,
    new_campaign,
    write_pack,
)


@pytest.fixture()
def real(app, seeded_db):
    with app.app_context():
        import_real_packs()
        yield


def oid(key):
    from app.models import CatalogObject

    return CatalogObject.query.filter_by(template_key=key).first().id


# ------------------------------------------------------------------- CRUD


def test_create_weapon_build_with_components(app, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(
        campaign, "weapon", base_object_id=oid("Shotgun_MG"), name="Close Assault", purpose="Max crit",
        attachments={("weapon_upgrade", 0): oid("LaserSightUpgrade"), ("weapon_upgrade", 1): oid("AutoLoaderUpgrade")},
    )
    view = builds.build_view(campaign, build)

    assert view.name == "Close Assault" and view.purpose == "Max crit"
    assert view.base.name == "Shard Gun"
    assert {c.ref.name for c in view.components} == {"Laser Sight", "Auto-Loader"}
    assert view.validation.is_valid


def test_create_armor_build(app, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(campaign, "armor", base_object_id=oid("PredatorArmor"), name="Mobile Scout", purpose="Scouting")
    view = builds.build_view(campaign, build)
    assert view.base.name == "Predator Armor" and view.type_label == "Armour Build"


def test_build_name_must_be_unique_per_campaign_and_type(app, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="Close Assault")
    with pytest.raises(builds.BuildValidationError):
        builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_MG"), name="Close Assault")
    builds.create_build(campaign, "armor", base_object_id=oid("PredatorArmor"), name="Close Assault")  # different type, OK


def test_build_requires_a_name(app, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    with pytest.raises(builds.BuildValidationError):
        builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="  ")


def test_update_build_changes_base_and_components(app, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="X")
    builds.update_build(
        campaign, build, base_object_id=oid("Shotgun_MG"), name="X",
        attachments={("weapon_upgrade", 0): oid("ScopeUpgrade")},
    )
    view = builds.build_view(campaign, build)
    assert view.base.name == "Shard Gun" and {c.ref.name for c in view.components} == {"Scope"}


def test_base_object_must_be_the_right_type(app, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    with pytest.raises(builds.BuildError):
        builds.create_build(campaign, "weapon", base_object_id=oid("PredatorArmor"), name="X")
    with pytest.raises(builds.BuildError):
        builds.create_build(campaign, "armor", base_object_id=oid("Shotgun_CV"), name="X")
    with pytest.raises(builds.BuildError):
        builds.get_build_type("gadget")


# ---------------------------------------------------------------- slots


def test_slot_count_comes_from_catalog_attribute(app, real):
    from app.services import builds
    from app.services.builds import BuildContext, slot_specs

    campaign = new_campaign("A", "wotc-standard")
    ctx = BuildContext(campaign)
    assert len(slot_specs(ctx, "weapon", oid("Shotgun_CV"))) == 1  # upgrade_slots: 1
    assert len(slot_specs(ctx, "weapon", oid("Shotgun_MG"))) == 2  # upgrade_slots: 2
    assert len(slot_specs(ctx, "weapon", oid("Pistol_CV"))) == 0  # upgrade_slots: 0
    assert all(s.verified for s in slot_specs(ctx, "weapon", oid("Shotgun_MG")))
    assert len(slot_specs(ctx, "armor", oid("PredatorArmor"))) == 2  # utility_slots: 2
    del builds


def test_unknown_slot_count_offers_default_but_flags_unverified(app, real, tmp_path):
    from app.services.builds import BuildContext, slot_specs
    from app.services.content_import import import_pack

    objects = [{"template_key": "MysteryGun", "type": "weapon", "name": "Mystery Gun", "attributes": {"weapon_category": "rifle"}}]
    import_pack(str(write_pack(tmp_path, "mystery", objects=objects, requires=["xcom2-base"])))
    campaign = new_campaign("A", "wotc-standard")
    attach_source(campaign, "mystery")
    specs = slot_specs(BuildContext(campaign), "weapon", oid("MysteryGun"))
    assert specs and all(not s.verified for s in specs)


def test_attaching_to_a_nonexistent_slot_is_rejected(app, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    with pytest.raises(builds.BuildValidationError):
        builds.create_build(
            campaign, "weapon", base_object_id=oid("Pistol_CV"), name="X",
            attachments={("weapon_upgrade", 0): oid("ScopeUpgrade")},
        )


# --------------------------------------------------------- compatibility


def test_invalid_component_type_rejected(app, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    with pytest.raises(builds.BuildValidationError):
        builds.create_build(
            campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="X",
            attachments={("weapon_upgrade", 0): oid("PredatorArmor")},  # not a weapon_upgrade
        )


def test_incompatible_component_rejected_and_override_accepted(app, real, tmp_path):
    from app.services import builds
    from app.services.content_import import import_pack

    objects = [{"template_key": "SniperOnlyScope", "type": "weapon_upgrade", "name": "Sniper-Only Scope",
                "attributes": {"compatible_weapon_categories": "sniper_rifle"}}]
    import_pack(str(write_pack(tmp_path, "scopes", objects=objects, requires=["xcom2-base"])))
    campaign = new_campaign("A", "wotc-standard")
    attach_source(campaign, "scopes")

    with pytest.raises(builds.BuildValidationError):
        builds.create_build(
            campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="X",
            attachments={("weapon_upgrade", 0): oid("SniperOnlyScope")},
        )
    build = builds.create_build(
        campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="Override Build",
        attachments={("weapon_upgrade", 0): oid("SniperOnlyScope")}, overrides={("weapon_upgrade", 0)},
    )
    view = builds.build_view(campaign, build)
    assert view.validation.is_valid
    assert view.components[0].is_override


def test_unverified_component_is_allowed_without_override(app, real):
    """The curated base pack has no compatible_weapon_categories data for its
    own upgrades, so they attach cleanly, flagged unverified -- not blocked."""
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(
        campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="X",
        attachments={("weapon_upgrade", 0): oid("ScopeUpgrade")},
    )
    view = builds.build_view(campaign, build)
    assert view.validation.is_valid and not view.components[0].is_override
    assert view.components[0].compatibility.state == "unverified"


def test_mutually_exclusive_components_rejected(app, real, tmp_path):
    from app.services import builds
    from app.services.content_import import import_pack

    objects = [
        {"template_key": "SightA", "type": "weapon_upgrade", "name": "Sight A", "attributes": {"exclusive_group": "sight"}},
        {"template_key": "SightB", "type": "weapon_upgrade", "name": "Sight B", "attributes": {"exclusive_group": "sight"}},
    ]
    import_pack(str(write_pack(tmp_path, "sights", objects=objects, requires=["xcom2-base"])))
    campaign = new_campaign("A", "wotc-standard")
    attach_source(campaign, "sights")
    with pytest.raises(builds.BuildValidationError):
        builds.create_build(
            campaign, "weapon", base_object_id=oid("Shotgun_MG"), name="X",
            attachments={("weapon_upgrade", 0): oid("SightA"), ("weapon_upgrade", 1): oid("SightB")},
        )


def test_duplicate_restriction_and_explicit_allowance(app, real, tmp_path):
    from app.services import builds
    from app.services.content_import import import_pack

    objects = [
        {"template_key": "UniqueMod", "type": "weapon_upgrade", "name": "Unique Mod", "attributes": {"allows_duplicates_in_build": False}},
        {"template_key": "StackableMod", "type": "weapon_upgrade", "name": "Stackable Mod", "attributes": {"allows_duplicates_in_build": True}},
    ]
    import_pack(str(write_pack(tmp_path, "mods", objects=objects, requires=["xcom2-base"])))
    campaign = new_campaign("A", "wotc-standard")
    attach_source(campaign, "mods")

    with pytest.raises(builds.BuildValidationError):
        builds.create_build(
            campaign, "weapon", base_object_id=oid("Shotgun_MG"), name="X",
            attachments={("weapon_upgrade", 0): oid("UniqueMod"), ("weapon_upgrade", 1): oid("UniqueMod")},
        )
    build = builds.create_build(
        campaign, "weapon", base_object_id=oid("Shotgun_MG"), name="Y",
        attachments={("weapon_upgrade", 0): oid("StackableMod"), ("weapon_upgrade", 1): oid("StackableMod")},
    )
    assert builds.build_view(campaign, build).validation.is_valid


def test_compatible_with_relationship_marks_compatibility(app, real, tmp_path):
    from app.services import builds
    from app.services.content_import import import_pack

    objects = [{"template_key": "PairedScope", "type": "weapon_upgrade", "name": "Paired Scope"}]
    rels = [{"type": "compatible_with", "from": ["paired", "PairedScope"], "to": ["xcom2-base", "Shotgun_MG"]}]
    import_pack(str(write_pack(tmp_path, "paired", objects=objects, relationships=rels, requires=["xcom2-base"])))
    campaign = new_campaign("A", "wotc-standard")
    attach_source(campaign, "paired")

    specs, options = builds.list_component_options(campaign, "weapon", oid("Shotgun_MG"))
    option = next(o for o in options["weapon_upgrade"] if o.ref.name == "Paired Scope")
    assert option.compatibility.state == "compatible"
    other_specs, other_options = builds.list_component_options(campaign, "weapon", oid("Shotgun_CV"))
    other_option = next(o for o in other_options["weapon_upgrade"] if o.ref.name == "Paired Scope")
    assert other_option.compatibility.state == "incompatible"
    del specs, other_specs


def test_component_outside_campaign_sources_rejected(app, real, tmp_path):
    from app.models import CatalogObject
    from app.services import builds
    from app.services.content_import import import_pack

    import_pack(str(write_pack(tmp_path, "foreign-mods", objects=[{"template_key": "ForeignMod", "type": "weapon_upgrade", "name": "Foreign Mod"}], requires=["xcom2-base"])))
    campaign = new_campaign("A", "wotc-standard")  # does not enable foreign-mods
    foreign_id = CatalogObject.query.filter_by(template_key="ForeignMod").first().id
    with pytest.raises(builds.BuildValidationError):
        builds.create_build(
            campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="X",
            attachments={("weapon_upgrade", 0): foreign_id},
        )


# --------------------------------------------------------- mod/custom objects


def test_custom_user_authored_weapon_and_upgrade_usable_in_a_build(app, real):
    from app.models import CatalogObjectType
    from app.repositories import content_sources as sources_repo
    from app.services import builds
    from app.services import catalog_objects as objects_service

    campaign = new_campaign("A", "wotc-standard")
    attach_source(campaign, "user-local")
    user_source = sources_repo.get_content_source_by_slug("user-local")
    weapon_type = CatalogObjectType.query.filter_by(slug="weapon").first()
    upgrade_type = CatalogObjectType.query.filter_by(slug="weapon_upgrade").first()

    custom_weapon = objects_service.create_object(
        source_id=user_source.id, type_id=weapon_type.id, name="XM8 Plasma Rifle", is_user_authored=True,
        attributes={"weapon_category": "rifle", "upgrade_slots": 2},
    )
    custom_upgrade = objects_service.create_object(
        source_id=user_source.id, type_id=upgrade_type.id, name="Custom Scope", is_user_authored=True,
    )
    build = builds.create_build(
        campaign, "weapon", base_object_id=custom_weapon.id, name="Custom Build",
        attachments={("weapon_upgrade", 0): custom_upgrade.id},
    )
    view = builds.build_view(campaign, build)
    assert view.base.name == "XM8 Plasma Rifle" and view.components[0].ref.name == "Custom Scope"
    assert view.validation.is_valid


# ----------------------------------------------------------------- reuse


def test_assigning_a_build_to_multiple_soldiers_does_not_duplicate_it(app, real):
    from app.services import builds, soldiers

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="Shared Build")
    jane = soldiers.create_soldier(campaign, name="Jane")
    tom = soldiers.create_soldier(campaign, name="Tom")
    soldiers.assign_build(campaign, jane, build)
    soldiers.assign_build(campaign, tom, build)

    assert builds.list_builds(campaign, "weapon") == [build]
    assert {sid for sid, _name in builds.build_view(campaign, build).assigned} == {jane.id, tom.id}


def test_editing_a_shared_build_is_seen_by_every_assignment_immediately(app, real):
    """Assignments reference the live build, not a snapshot (ADR-0022)."""
    from app.services import builds, soldiers

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="Evolving Build")
    jane = soldiers.create_soldier(campaign, name="Jane")
    soldiers.assign_build(campaign, jane, build)

    builds.update_build(campaign, build, base_object_id=oid("Shotgun_MG"), name="Evolving Build")

    assigned_view = soldiers.soldier_view(campaign, jane).assignment("weapon")
    assert assigned_view.base.name == "Shard Gun"  # the soldier immediately sees the new version


def test_reassigning_a_soldiers_weapon_build_replaces_the_previous_one(app, real):
    from app.services import builds, soldiers

    campaign = new_campaign("A", "wotc-standard")
    first = builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="First")
    second = builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_MG"), name="Second")
    jane = soldiers.create_soldier(campaign, name="Jane")
    soldiers.assign_build(campaign, jane, first)
    soldiers.assign_build(campaign, jane, second)

    assert soldiers.soldier_view(campaign, jane).assignment("weapon").id == second.id


def test_unassigning_a_build_clears_the_slot(app, real):
    from app.services import builds, soldiers

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="X")
    jane = soldiers.create_soldier(campaign, name="Jane")
    soldiers.assign_build(campaign, jane, build)
    soldiers.unassign_build(campaign, jane, "weapon")
    assert soldiers.soldier_view(campaign, jane).assignment("weapon") is None


# ---------------------------------------------------- availability/progression


def test_build_with_unresearched_equipment_is_planned_not_blocked(app, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")  # Plasma-tier weapon not yet unlocked
    plasma_rifle = oid("AssaultRifle_BM")
    build = builds.create_build(campaign, "weapon", base_object_id=plasma_rifle, name="Experimental")
    view = builds.build_view(campaign, build)

    assert not view.is_available and view.availability_label == "PLANNED / NOT YET AVAILABLE"
    assert view.missing and view.missing[0].object_id == plasma_rifle


def test_unlocking_the_base_object_flips_the_build_to_available(app, real):
    from app.services import builds, research

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(campaign, "weapon", base_object_id=oid("AssaultRifle_MG"), name="Mag Rifle Build")
    assert not builds.build_view(campaign, build).is_available

    for key in ("AlienBiotech", "AutopsyAdventOfficer", "HybridMaterials", "ModularWeapons", "MagnetizedWeapons"):
        research.complete_research(campaign, oid(key))
    assert builds.build_view(campaign, build).is_available


# -------------------------------------------------------------- deletion


def test_deleting_unassigned_build_succeeds(app, real):
    from app.extensions import db
    from app.models import CampaignBuild
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="X")
    build_id = build.id
    builds.delete_build(campaign, build)
    assert db.session.get(CampaignBuild, build_id) is None


def test_deleting_assigned_build_is_refused_without_explicit_unassign(app, real):
    from app.services import builds, soldiers

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="X")
    jane = soldiers.create_soldier(campaign, name="Jane")
    soldiers.assign_build(campaign, jane, build)

    with pytest.raises(builds.BuildInUseError, match="Jane"):
        builds.delete_build(campaign, build)

    builds.delete_build(campaign, build, unassign=True)
    assert soldiers.soldier_view(campaign, jane).assignment("weapon") is None


def test_build_belongs_to_one_campaign_only(app, real):
    from app.services import builds

    a, b = new_campaign("A", "wotc-standard"), new_campaign("B", "wotc-standard")
    build = builds.create_build(a, "weapon", base_object_id=oid("Shotgun_CV"), name="X")
    assert builds.get_build(b, build.id) is None
    with pytest.raises(builds.BuildError):
        builds.update_build(b, build, base_object_id=oid("Shotgun_CV"), name="Hacked")


def test_deleting_campaign_removes_only_its_own_builds(app, real):
    from app.extensions import db
    from app.models import CampaignBuild
    from app.services import builds, campaign_lifecycle

    a, b = new_campaign("A", "wotc-standard"), new_campaign("B", "wotc-standard")
    builds.create_build(a, "weapon", base_object_id=oid("Shotgun_CV"), name="X")
    builds.create_build(b, "weapon", base_object_id=oid("Shotgun_CV"), name="X")
    a_id, b_id = a.id, b.id

    campaign_lifecycle.delete_campaign(a)
    db.session.expire_all()

    assert CampaignBuild.query.filter_by(campaign_id=a_id).count() == 0
    assert CampaignBuild.query.filter_by(campaign_id=b_id).count() == 1


def test_preview_build_does_not_persist_anything(app, real):
    from app.models import CampaignBuild
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    preview = builds.preview_build(
        campaign, "weapon", base_object_id=oid("Shotgun_MG"),
        attachments={("weapon_upgrade", 0): oid("ScopeUpgrade")},
    )
    assert preview.validation.is_valid and preview.base.name == "Shard Gun"
    assert CampaignBuild.query.count() == 0
