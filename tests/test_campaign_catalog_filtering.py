"""Campaign catalog filtering (FR-CMP-2/3, acceptance criterion #10: "switch
campaigns and see catalog content filtered by applicable content sources")."""
from __future__ import annotations

from app.models import Campaign, CatalogObjectType, ContentSource


def test_switching_active_campaign_changes_catalog_source_filter(client, app, seeded_db):
    with app.app_context():
        from app.services import campaign_lifecycle
        from app.services import catalog_objects as objects_service

        base_source = ContentSource.query.filter_by(slug="xcom2-base").first()
        lwotc_source = ContentSource.query.filter_by(slug="lwotc").first()
        weapon_type = CatalogObjectType.query.filter_by(slug="weapon").first()

        base_obj = objects_service.create_object(
            source_id=base_source.id, type_id=weapon_type.id, name="Vanilla Rifle"
        )
        lwotc_obj = objects_service.create_object(
            source_id=lwotc_source.id, type_id=weapon_type.id, name="LWotC Exclusive Rifle"
        )

        campaign_vanilla = campaign_lifecycle.create_campaign(
            name="Commander Vanilla", game_profile_slug="wotc-standard"
        )
        campaign_lwotc = campaign_lifecycle.create_campaign(
            name="LWOTC 2026", game_profile_slug="lwotc"
        )

        vanilla_id, lwotc_id = campaign_vanilla.id, campaign_lwotc.id
        _base_obj_id, _lwotc_obj_id = base_obj.id, lwotc_obj.id

    # Select the vanilla campaign -- catalog should show the base object but
    # not the LWotC-exclusive one (LWotC source not enabled for this campaign).
    client.post(f"/campaigns/{vanilla_id}/select")
    response_vanilla = client.get("/catalog/")
    assert b"Vanilla Rifle" in response_vanilla.data
    assert b"LWotC Exclusive Rifle" not in response_vanilla.data

    # Switch to the LWotC campaign -- now both are visible (its sources
    # include xcom2-base, wotc AND lwotc).
    client.post(f"/campaigns/{lwotc_id}/select")
    response_lwotc = client.get("/catalog/")
    assert b"Vanilla Rifle" in response_lwotc.data
    assert b"LWotC Exclusive Rifle" in response_lwotc.data


def test_catalog_source_filter_dropdown_only_lists_enabled_sources(client, app, seeded_db):
    with app.app_context():
        from app.services import campaign_lifecycle

        campaign = campaign_lifecycle.create_campaign(name="Commander Vanilla", game_profile_slug="wotc-standard")
        campaign_id = campaign.id

    client.post(f"/campaigns/{campaign_id}/select")
    response = client.get("/catalog/")

    assert b"XCOM 2" in response.data
    assert b"War of the Chosen" in response.data
    # LWotC is not attached to this campaign's enabled sources, so the
    # per-campaign filter list should not offer it.
    assert b"disabled for this campaign" in response.data or b"Long War of the Chosen" not in response.data


def test_two_campaigns_with_different_sources_remain_isolated(app, seeded_db, db):
    """Exercises the Phase 3 brief's example literally:
    Campaign 'Commander Vanilla' -> XCOM 2, WotC
    Campaign 'LWOTC 2026' -> XCOM 2, WotC, LWotC, community mods
    Catalog objects are never duplicated per campaign; only visibility differs."""
    with app.app_context():
        from app.services import campaign_lifecycle
        from app.services import campaign_sources as campaign_sources_service
        from app.services import content_sources as sources_service

        community_mod = sources_service.create_content_source(name="Community Mod X", kind="workshop_mod")

        vanilla = campaign_lifecycle.create_campaign(name="Commander Vanilla", game_profile_slug="wotc-standard")
        lwotc_campaign = campaign_lifecycle.create_campaign(name="LWOTC 2026", game_profile_slug="lwotc")
        campaign_sources_service.add_source_to_campaign(lwotc_campaign, community_mod)

        from app.models import CatalogObject

        objects_count_before = CatalogObject.query.count()

        # Catalog objects are source-scoped, not campaign-scoped -- there is
        # exactly one catalog, never duplicated per campaign.
        assert objects_count_before == 0  # no objects created in this test yet

        vanilla_sources = {cs.content_source.slug for cs in vanilla.content_sources}
        lwotc_sources = {cs.content_source.slug for cs in lwotc_campaign.content_sources}

        assert vanilla_sources == {"xcom2-base", "wotc"}
        assert lwotc_sources == {"xcom2-base", "wotc", "lwotc", community_mod.slug}

        # Deleting the vanilla campaign must not touch the LWotC campaign's
        # source associations or the catalog/content sources themselves.
        from app.models import ContentSource

        content_source_count_before = ContentSource.query.count()
        campaign_lifecycle.delete_campaign(vanilla)
        content_source_count_after = ContentSource.query.count()

        assert content_source_count_before == content_source_count_after
        reloaded_lwotc = db.session.get(Campaign, lwotc_campaign.id)
        assert reloaded_lwotc is not None
        assert {cs.content_source.slug for cs in reloaded_lwotc.content_sources} == lwotc_sources
