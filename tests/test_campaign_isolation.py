"""Campaign isolation tests (ADR-0010, PRODUCT_SPEC success criterion #1 & #5).

Demonstrates:
- Campaign A and Campaign B remain independent.
- Updating Campaign A does not alter Campaign B.
- Deleting Campaign A does not delete global catalog definitions.
- Campaign-specific records cannot accidentally be retrieved for another campaign.
"""
from __future__ import annotations

from app.extensions import db
from app.models import (
    Campaign,
    CampaignContentSource,
    CampaignObjectState,
    CatalogObject,
    CatalogObjectType,
    ContentSource,
)
from app.services import campaign_lifecycle


def _make_catalog_object(source: ContentSource, object_type: CatalogObjectType, template_key: str) -> CatalogObject:
    obj = CatalogObject(
        source_id=source.id,
        type_id=object_type.id,
        template_key=template_key,
        slug=template_key.lower(),
        name=f"Test Object {template_key}",
    )
    db.session.add(obj)
    db.session.flush()
    return obj


def test_two_campaigns_have_independent_object_state(app, seeded_db):
    with app.app_context():
        source = ContentSource.query.filter_by(slug="xcom2-base").first()
        obj_type = CatalogObjectType.query.filter_by(slug="research_project").first()
        research_object = _make_catalog_object(source, obj_type, "MagneticWeapons")
        db.session.commit()

        campaign_a = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        campaign_b = campaign_lifecycle.create_campaign(name="Campaign B", game_profile_slug=None)

        state_a = CampaignObjectState(
            campaign_id=campaign_a.id,
            catalog_object_id=research_object.id,
            status="completed",
        )
        db.session.add(state_a)
        db.session.commit()

        # Campaign B has no state row for the same catalog object.
        state_for_b = CampaignObjectState.query.filter_by(
            campaign_id=campaign_b.id, catalog_object_id=research_object.id
        ).first()
        assert state_for_b is None

        # Campaign A's state is exactly what we wrote, untouched by B's absence of state.
        reloaded_a = CampaignObjectState.query.filter_by(
            campaign_id=campaign_a.id, catalog_object_id=research_object.id
        ).first()
        assert reloaded_a.status == "completed"


def test_updating_campaign_a_does_not_alter_campaign_b(app, seeded_db):
    with app.app_context():
        campaign_a = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        campaign_b = campaign_lifecycle.create_campaign(name="Campaign B", game_profile_slug=None)

        b_snapshot = {
            "name": campaign_b.name,
            "difficulty": campaign_b.difficulty,
            "is_ironman": campaign_b.is_ironman,
            "notes": campaign_b.notes,
            "updated_at": campaign_b.updated_at,
        }

        campaign_lifecycle.update_campaign(
            campaign_a,
            name="Campaign A (renamed)",
            difficulty="legend",
            is_ironman=True,
            notes="Changed only on A.",
        )

        reloaded_b = db.session.get(Campaign, campaign_b.id)
        assert reloaded_b.name == b_snapshot["name"]
        assert reloaded_b.difficulty == b_snapshot["difficulty"]
        assert reloaded_b.is_ironman == b_snapshot["is_ironman"]
        assert reloaded_b.notes == b_snapshot["notes"]


def test_deleting_campaign_does_not_delete_catalog_or_other_campaign(app, seeded_db):
    with app.app_context():
        source = ContentSource.query.filter_by(slug="xcom2-base").first()
        obj_type = CatalogObjectType.query.filter_by(slug="weapon").first()
        shared_object = _make_catalog_object(source, obj_type, "MagRifle")
        db.session.commit()

        catalog_object_count_before = CatalogObject.query.count()
        content_source_count_before = ContentSource.query.count()

        campaign_a = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug="wotc-standard")
        campaign_b = campaign_lifecycle.create_campaign(name="Campaign B", game_profile_slug="wotc-standard")

        db.session.add(
            CampaignObjectState(
                campaign_id=campaign_a.id, catalog_object_id=shared_object.id, status="owned"
            )
        )
        db.session.add(
            CampaignObjectState(
                campaign_id=campaign_b.id, catalog_object_id=shared_object.id, status="locked"
            )
        )
        db.session.commit()

        campaign_b_id = campaign_b.id

        campaign_lifecycle.delete_campaign(campaign_a)

        # The catalog is completely untouched.
        assert CatalogObject.query.count() == catalog_object_count_before
        assert ContentSource.query.count() == content_source_count_before
        assert db.session.get(CatalogObject, shared_object.id) is not None

        # Campaign B and its own state row survive, unchanged.
        reloaded_b = db.session.get(Campaign, campaign_b_id)
        assert reloaded_b is not None
        assert reloaded_b.name == "Campaign B"
        state_b = CampaignObjectState.query.filter_by(
            campaign_id=campaign_b_id, catalog_object_id=shared_object.id
        ).first()
        assert state_b is not None
        assert state_b.status == "locked"

        # Campaign A's own rows are gone.
        assert CampaignObjectState.query.filter_by(campaign_id=campaign_a.id).count() == 0
        assert (
            CampaignContentSource.query.filter_by(campaign_id=campaign_a.id).count() == 0
        )


def test_campaign_object_state_lookup_is_scoped_to_campaign(app, seeded_db):
    """A state row belonging to campaign A must never be retrievable by
    passing campaign B's id, even if the surrogate row id is known/guessed."""
    with app.app_context():
        source = ContentSource.query.filter_by(slug="xcom2-base").first()
        obj_type = CatalogObjectType.query.filter_by(slug="facility").first()
        facility = _make_catalog_object(source, obj_type, "ProvingGround")
        db.session.commit()

        campaign_a = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        campaign_b = campaign_lifecycle.create_campaign(name="Campaign B", game_profile_slug=None)

        state_a = CampaignObjectState(
            campaign_id=campaign_a.id, catalog_object_id=facility.id, status="in_progress"
        )
        db.session.add(state_a)
        db.session.commit()

        # Simulate the ADR-0010-mandated lookup pattern: campaign_id is always
        # part of the filter, so the same surrogate id under the wrong
        # campaign_id returns nothing.
        leaked = CampaignObjectState.query.filter_by(
            id=state_a.id, campaign_id=campaign_b.id
        ).first()
        assert leaked is None

        correctly_scoped = CampaignObjectState.query.filter_by(
            id=state_a.id, campaign_id=campaign_a.id
        ).first()
        assert correctly_scoped is not None


def test_deleting_campaign_does_not_affect_another_campaigns_source_associations(app, seeded_db):
    """Phase 3: campaign <-> content-source associations (campaign_content_sources)
    are campaign-scoped data too -- deleting campaign A must leave campaign B's
    enabled-source list completely untouched."""
    with app.app_context():
        from app.services import campaign_sources as campaign_sources_service

        source_a = ContentSource.query.filter_by(slug="xcom2-base").first()
        source_b = ContentSource.query.filter_by(slug="wotc").first()

        campaign_a = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        campaign_b = campaign_lifecycle.create_campaign(name="Campaign B", game_profile_slug=None)

        campaign_sources_service.add_source_to_campaign(campaign_a, source_a)
        campaign_sources_service.add_source_to_campaign(campaign_b, source_a)
        campaign_sources_service.add_source_to_campaign(campaign_b, source_b)

        campaign_b_id = campaign_b.id
        campaign_lifecycle.delete_campaign(campaign_a)

        reloaded_b = db.session.get(Campaign, campaign_b_id)
        assert reloaded_b is not None
        remaining_slugs = {cs.content_source.slug for cs in reloaded_b.content_sources}
        assert remaining_slugs == {"xcom2-base", "wotc"}

        # And the content sources themselves are obviously untouched too.
        assert ContentSource.query.filter_by(slug="xcom2-base").first() is not None
        assert ContentSource.query.filter_by(slug="wotc").first() is not None


def test_catalog_relationships_survive_campaign_deletion(app, seeded_db):
    """Relationships are catalog-scoped (never reference a campaign,
    ADR-0002/ADR-0004); deleting every campaign that happens to exist must
    not cascade into the catalog's relationship graph."""
    with app.app_context():
        from app.models import CatalogRelationship, CatalogRelationshipType
        from app.services import catalog_relationships as relationships_service

        source = ContentSource.query.filter_by(slug="xcom2-base").first()
        obj_type = CatalogObjectType.query.filter_by(slug="weapon").first()
        obj_a = _make_catalog_object(source, obj_type, "RelObjA")
        obj_b = _make_catalog_object(source, obj_type, "RelObjB")
        db.session.commit()

        rel_type = CatalogRelationshipType.query.filter_by(slug="compatible_with").first()
        relationships_service.create_relationship(
            relationship_type=rel_type, source_object=obj_a, target_object=obj_b, source_pack_id=source.id
        )
        relationship_count_before = CatalogRelationship.query.count()

        campaign = campaign_lifecycle.create_campaign(name="Ephemeral Campaign", game_profile_slug=None)
        campaign_lifecycle.delete_campaign(campaign)

        relationship_count_after = CatalogRelationship.query.count()
        assert relationship_count_before == relationship_count_after == 1
