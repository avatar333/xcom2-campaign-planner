"""Phase 4 "Definition of done" acceptance test (`docs/IMPLEMENTATION_PLAN.md`
§4): Product Spec success criterion #1 ("create two campaigns, different
sources, independent state") proven against *real* imported curated catalog
data, with at least one genuine cross-source override exercised by the
effective-catalog resolver -- `PlasmaRifle_LWOTC` overriding the base
`PlasmaRifle` research project, which a WotC-standard campaign must never
see and an LWotC campaign must always see instead."""
from __future__ import annotations

from pathlib import Path

PACKS_ROOT = Path(__file__).resolve().parent.parent / "content" / "packs"


def _import_all_real_packs():
    from app.cli import _discover_pack_slugs, _pack_dir_for
    from app.services.content_import import import_pack

    for slug in _discover_pack_slugs():
        import_pack(str(_pack_dir_for(slug)))


def test_wotc_and_lwotc_campaigns_see_different_plasma_rifle_definitions(app, seeded_db):
    with app.app_context():
        _import_all_real_packs()

        from app.models import CatalogObject, ContentSource
        from app.services import campaign_lifecycle
        from app.services import effective_catalog as effective_catalog_service

        base_source = ContentSource.query.filter_by(slug="xcom2-base").first()
        lwotc_source = ContentSource.query.filter_by(slug="lwotc").first()
        assert base_source is not None and lwotc_source is not None

        base_plasma = CatalogObject.query.filter_by(
            source_id=base_source.id, template_key="PlasmaRifle"
        ).first()
        lwotc_plasma = CatalogObject.query.filter_by(
            source_id=lwotc_source.id, template_key="PlasmaRifle_LWOTC"
        ).first()
        assert base_plasma is not None
        assert lwotc_plasma is not None
        assert lwotc_plasma.overrides_object_id == base_plasma.id

        wotc_campaign = campaign_lifecycle.create_campaign(
            name="Standard Campaign", game_profile_slug="wotc-standard"
        )
        lwotc_campaign = campaign_lifecycle.create_campaign(
            name="LWotC Campaign", game_profile_slug="lwotc"
        )

        wotc_effective = effective_catalog_service.get_effective_catalog(
            wotc_campaign.id, version=wotc_campaign.effective_catalog_version
        )
        lwotc_effective = effective_catalog_service.get_effective_catalog(
            lwotc_campaign.id, version=lwotc_campaign.effective_catalog_version
        )

        # WotC-standard campaign: sees the base Plasma Rifle, not the LWotC override.
        assert base_plasma.id in wotc_effective
        assert lwotc_plasma.id not in wotc_effective

        # LWotC campaign: the override wins; the base row is shadowed. The
        # merged fields reflect the overriding object's identity (id,
        # source_id, template_key) with its `patch` applied over the base's
        # other fields (ADR-0013) -- the LWotC pack only patches
        # `attributes.ruleset_note`, so `name` is inherited from the base.
        assert lwotc_plasma.id in lwotc_effective
        assert base_plasma.id not in lwotc_effective
        winning = lwotc_effective[lwotc_plasma.id]
        assert winning["source_id"] == lwotc_source.id
        assert winning["template_key"] == "PlasmaRifle_LWOTC"
        assert "ruleset_note" in winning["attributes"]
        assert "LWOTC" in winning["attributes"]["ruleset_note"]


def test_wotc_and_lwotc_campaign_state_is_fully_independent(app, seeded_db):
    with app.app_context():
        _import_all_real_packs()

        from app.extensions import db
        from app.models import CampaignObjectState, CatalogObject, ContentSource
        from app.services import campaign_lifecycle

        base_source = ContentSource.query.filter_by(slug="xcom2-base").first()
        research_obj = CatalogObject.query.filter_by(
            source_id=base_source.id, template_key="MagnetizedWeapons"
        ).first()
        assert research_obj is not None

        campaign_a = campaign_lifecycle.create_campaign(
            name="Campaign A", game_profile_slug="wotc-standard"
        )
        campaign_b = campaign_lifecycle.create_campaign(
            name="Campaign B", game_profile_slug="wotc-standard"
        )

        db.session.add(
            CampaignObjectState(
                campaign_id=campaign_a.id, catalog_object_id=research_obj.id, status="completed"
            )
        )
        db.session.commit()

        state_a = CampaignObjectState.query.filter_by(
            campaign_id=campaign_a.id, catalog_object_id=research_obj.id
        ).first()
        state_b = CampaignObjectState.query.filter_by(
            campaign_id=campaign_b.id, catalog_object_id=research_obj.id
        ).first()

        assert state_a is not None
        assert state_a.status == "completed"
        assert state_b is None  # campaign B is completely untouched
