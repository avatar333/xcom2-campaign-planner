"""Catalog-object CRUD, identity, attribute validation tests (FR-CAT-1/2/6/7/8)."""
from __future__ import annotations

import pytest

from app.models import CatalogObjectType, ContentSource
from app.repositories import catalog_objects as objects_repo
from app.services import catalog_objects as objects_service
from app.services.catalog_objects import CatalogObjectValidationError


def _weapon_type() -> CatalogObjectType:
    return CatalogObjectType.query.filter_by(slug="weapon").first()


def _user_source() -> ContentSource:
    return ContentSource.query.filter_by(slug="user-local").first()


def test_create_object_minimal(app, seeded_db):
    with app.app_context():
        source = _user_source()
        obj_type = _weapon_type()
        obj = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Example Rifle")

        assert obj.id is not None
        assert obj.name == "Example Rifle"
        assert obj.source_id == source.id
        assert obj.type_id == obj_type.id
        assert obj.template_key  # auto-generated from the name
        assert obj.slug
        assert obj.is_user_authored is False  # caller did not pass the flag


def test_create_object_auto_generates_unique_slug_and_template_key(app, seeded_db):
    with app.app_context():
        source = _user_source()
        obj_type = _weapon_type()
        first = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Duplicate Name")
        second = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Duplicate Name")

        assert first.slug != second.slug
        assert first.template_key != second.template_key


def test_create_object_requires_name(app, seeded_db):
    with app.app_context():
        source = _user_source()
        obj_type = _weapon_type()
        with pytest.raises(CatalogObjectValidationError):
            objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="   ")


def test_create_object_with_tags(app, seeded_db):
    with app.app_context():
        source = _user_source()
        obj_type = _weapon_type()
        obj = objects_service.create_object(
            source_id=source.id, type_id=obj_type.id, name="Tagged Weapon",
            tags=["primary", "Conventional", "primary"],
        )
        from app.models import CatalogObjectTag

        tag_rows = CatalogObjectTag.query.filter_by(object_id=obj.id).all()
        assert sorted(t.tag for t in tag_rows) == ["conventional", "primary"]


def test_update_object(app, seeded_db):
    with app.app_context():
        source = _user_source()
        obj_type = _weapon_type()
        obj = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Original Name")

        objects_service.update_object(obj, name="New Name", short_description="Updated desc", tier=3)

        assert obj.name == "New Name"
        assert obj.short_description == "Updated desc"
        assert obj.tier == 3


def test_update_object_requires_name(app, seeded_db):
    with app.app_context():
        source = _user_source()
        obj_type = _weapon_type()
        obj = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Keep Me")
        with pytest.raises(CatalogObjectValidationError):
            objects_service.update_object(obj, name="")


def test_hard_delete_object_with_no_references(app, seeded_db):
    with app.app_context():
        source = _user_source()
        obj_type = _weapon_type()
        obj = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Deletable")
        obj_id = obj.id

        status = objects_service.retire_or_delete(obj)

        assert status == "deleted"
        assert objects_repo.get_object(obj_id) is None


def test_retire_instead_of_delete_when_relationship_exists(app, seeded_db):
    with app.app_context():
        from app.models import CatalogRelationshipType
        from app.services import catalog_relationships as relationships_service

        source = _user_source()
        obj_type = _weapon_type()
        obj_a = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Object A")
        obj_b = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Object B")
        rel_type = CatalogRelationshipType.query.filter_by(slug="compatible_with").first()
        relationships_service.create_relationship(
            relationship_type=rel_type, source_object=obj_a, target_object=obj_b, source_pack_id=source.id
        )

        status = objects_service.retire_or_delete(obj_a)

        assert status == "retired"
        reloaded = objects_repo.get_object(obj_a.id)
        assert reloaded is not None
        assert reloaded.is_retired is True


def test_create_object_with_arbitrary_object_type(app, seeded_db):
    """Any seeded type works without a code change (FR-CAT-2)."""
    with app.app_context():
        source = _user_source()
        covert_action_type = CatalogObjectType.query.filter_by(slug="covert_action").first()
        obj = objects_service.create_object(
            source_id=source.id, type_id=covert_action_type.id, name="Rescue VIP"
        )
        assert obj.type_id == covert_action_type.id


def test_create_mod_object_in_dedicated_workshop_source(app, seeded_db):
    """The full 'Catalog -> Add Object, source: [WOTC] Some Weapon Pack' workflow."""
    with app.app_context():
        from app.services import content_sources as sources_service

        mod_source = sources_service.create_content_source(
            name="[WOTC] Some Weapon Pack", kind="workshop_mod", steam_workshop_id="123456789"
        )
        obj_type = _weapon_type()
        obj = objects_service.create_object(
            source_id=mod_source.id,
            type_id=obj_type.id,
            name="Example Rifle",
            template_key="ExampleRifleWP",
            tier=2,
            tags=["primary"],
            attributes={},
            is_user_authored=True,
        )

        assert obj.source_id == mod_source.id
        assert obj.is_user_authored is True
        assert obj.template_key == "ExampleRifleWP"


def test_duplicate_template_key_within_same_source_is_disambiguated(app, seeded_db):
    """Identity is (source_id, template_key); creating two objects that would
    collide on an explicit template_key gets a disambiguated key instead of
    silently colliding at the DB UNIQUE constraint."""
    with app.app_context():
        source = _user_source()
        obj_type = _weapon_type()
        first = objects_service.create_object(
            source_id=source.id, type_id=obj_type.id, name="A", template_key="SharedKey"
        )
        second = objects_service.create_object(
            source_id=source.id, type_id=obj_type.id, name="B", template_key="SharedKey"
        )
        assert first.template_key != second.template_key


def test_same_template_key_in_different_sources_does_not_collide(app, seeded_db):
    """Template keys are only unique within (source_id, template_key), not
    globally -- two unrelated sources may reuse the same key."""
    with app.app_context():
        from app.services import content_sources as sources_service

        source_a = sources_service.create_content_source(name="Mod A", kind="workshop_mod")
        source_b = sources_service.create_content_source(name="Mod B", kind="workshop_mod")
        obj_type = _weapon_type()

        obj_a = objects_service.create_object(
            source_id=source_a.id, type_id=obj_type.id, name="Laser Rifle", template_key="LaserRifle"
        )
        obj_b = objects_service.create_object(
            source_id=source_b.id, type_id=obj_type.id, name="Laser Rifle", template_key="LaserRifle"
        )

        assert obj_a.template_key == obj_b.template_key == "LaserRifle"
        assert obj_a.id != obj_b.id
        assert objects_repo.get_by_source_and_template_key(source_a.id, "LaserRifle").id == obj_a.id
        assert objects_repo.get_by_source_and_template_key(source_b.id, "LaserRifle").id == obj_b.id


def test_attributes_validated_against_type_definitions(app, seeded_db):
    with app.app_context():
        from app.repositories import object_types as object_types_repo

        source = _user_source()
        obj_type = _weapon_type()
        object_types_repo.create_attribute_definition(
            object_type_id=obj_type.id, key="damage_min", data_type="integer", is_required=False
        )

        obj = objects_service.create_object(
            source_id=source.id, type_id=obj_type.id, name="Validated Weapon",
            attributes={"damage_min": 5},
        )
        from app.domain.attributes import parse_attributes_json

        assert parse_attributes_json(obj.attributes) == {"damage_min": 5}


def test_unknown_attribute_key_is_rejected(app, seeded_db):
    with app.app_context():
        source = _user_source()
        obj_type = _weapon_type()
        with pytest.raises(CatalogObjectValidationError):
            objects_service.create_object(
                source_id=source.id, type_id=obj_type.id, name="Bad Attrs",
                attributes={"totally_unknown_key": 1},
            )


def test_create_overlay_does_not_mutate_base_object(app, seeded_db):
    """FR-CAT-6: built-in objects are read-only; editing creates an overlay."""
    with app.app_context():
        base_source = ContentSource.query.filter_by(slug="xcom2-base").first()
        obj_type = _weapon_type()
        base_obj = objects_service.create_object(
            source_id=base_source.id, type_id=obj_type.id, name="Base Rifle",
            short_description="Original description.",
        )

        user_source = _user_source()
        overlay = objects_service.create_overlay(
            base_obj, user_source_id=user_source.id, name="My Tweaked Rifle"
        )

        reloaded_base = objects_repo.get_object(base_obj.id)
        assert reloaded_base.name == "Base Rifle"
        assert reloaded_base.short_description == "Original description."

        assert overlay.overrides_object_id == base_obj.id
        assert overlay.source_id == user_source.id
        assert overlay.is_user_authored is True
        assert overlay.name == "My Tweaked Rifle"


def test_is_referenced_by_campaign_state_detection(app, seeded_db):
    with app.app_context():
        from app.models import CampaignObjectState

        source = _user_source()
        obj_type = _weapon_type()
        obj = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Stateful")

        assert objects_repo.is_referenced_by_campaign_state(obj) is False

        from app.extensions import db
        from app.services import campaign_lifecycle

        campaign = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        db.session.add(
            CampaignObjectState(campaign_id=campaign.id, catalog_object_id=obj.id, status="owned")
        )
        db.session.commit()

        assert objects_repo.is_referenced_by_campaign_state(obj) is True
