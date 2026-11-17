"""Content-source CRUD + campaign association tests (FR-SRC-1/3/6, FR-CMP-2/3)."""
from __future__ import annotations

import pytest

from app.models import Campaign
from app.repositories import campaign_sources as campaign_sources_repo
from app.repositories import content_sources as sources_repo
from app.services import campaign_sources as campaign_sources_service
from app.services import content_sources as sources_service
from app.services.campaign_sources import CampaignSourceError
from app.services.content_sources import ContentSourceInUseError, ContentSourceValidationError


def test_create_content_source_assigns_unique_slug(app, seeded_db):
    with app.app_context():
        source = sources_service.create_content_source(name="XCOM 2", kind="workshop_mod")
        # "xcom2-base" already exists with slug "xcom2-base"; this source's
        # name collides only in slugified form with nothing pre-existing
        # named exactly "XCOM 2", so it should get a clean slug.
        assert source.slug
        assert source.kind == "workshop_mod"
        assert source.is_builtin is False


def test_create_content_source_deduplicates_slug(app, seeded_db):
    with app.app_context():
        first = sources_service.create_content_source(name="Better Pistols", kind="workshop_mod")
        second = sources_service.create_content_source(name="Better Pistols", kind="workshop_mod")
        assert first.slug != second.slug
        assert second.slug.startswith(first.slug)


def test_create_content_source_requires_name(app, seeded_db):
    with app.app_context(), pytest.raises(ContentSourceValidationError):
        sources_service.create_content_source(name="   ", kind="workshop_mod")


def test_create_content_source_rejects_invalid_kind(app, seeded_db):
    with app.app_context(), pytest.raises(ContentSourceValidationError):
        sources_service.create_content_source(name="Bad Kind", kind="not_a_real_kind")


def test_steam_workshop_id_is_optional(app, seeded_db):
    with app.app_context():
        source = sources_service.create_content_source(name="No Workshop ID", kind="user")
        assert source.steam_workshop_id is None


def test_update_content_source(app, seeded_db):
    with app.app_context():
        source = sources_service.create_content_source(name="Original", kind="workshop_mod")
        updated = sources_service.update_content_source(
            source, name="Renamed", kind="user", version="2.0.0"
        )
        assert updated.name == "Renamed"
        assert updated.kind == "user"
        assert updated.version == "2.0.0"


def test_delete_content_source_without_objects_succeeds(app, seeded_db):
    with app.app_context():
        source = sources_service.create_content_source(name="Deletable", kind="workshop_mod")
        source_id = source.id
        sources_service.delete_content_source(source)
        assert sources_repo.get_content_source(source_id) is None


def test_delete_builtin_content_source_is_rejected(app, seeded_db):
    with app.app_context():
        builtin = sources_repo.get_content_source_by_slug("xcom2-base")
        with pytest.raises(ContentSourceInUseError):
            sources_service.delete_content_source(builtin)


def test_delete_content_source_with_objects_is_rejected(app, seeded_db):
    with app.app_context():
        from app.models import CatalogObjectType
        from app.services import catalog_objects as objects_service

        source = sources_service.create_content_source(name="Has Objects", kind="workshop_mod")
        obj_type = CatalogObjectType.query.filter_by(slug="weapon").first()
        objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Thing")

        with pytest.raises(ContentSourceInUseError):
            sources_service.delete_content_source(source)


def test_delete_content_source_attached_to_campaign_is_rejected(app, seeded_db):
    with app.app_context():
        from app.services import campaign_lifecycle

        source = sources_service.create_content_source(name="Attached Source", kind="workshop_mod")
        campaign = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        campaign_sources_service.add_source_to_campaign(campaign, source)

        with pytest.raises(ContentSourceInUseError):
            sources_service.delete_content_source(source)


def test_get_or_create_user_source_reuses_seeded_source(app, seeded_db):
    with app.app_context():
        source = sources_service.get_or_create_user_source()
        assert source.slug == "user-local"


# ---- Campaign <-> content-source association (FR-CMP-2/3) ----


def test_add_source_to_campaign(app, seeded_db):
    with app.app_context():
        from app.services import campaign_lifecycle

        campaign = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        source = sources_repo.get_content_source_by_slug("xcom2-base")

        campaign_sources_service.add_source_to_campaign(campaign, source)

        associations = campaign_sources_repo.list_for_campaign(campaign.id)
        assert len(associations) == 1
        assert associations[0].content_source_id == source.id
        assert associations[0].enabled is True


def test_add_duplicate_source_to_campaign_is_rejected(app, seeded_db):
    with app.app_context():
        from app.services import campaign_lifecycle

        campaign = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        source = sources_repo.get_content_source_by_slug("xcom2-base")
        campaign_sources_service.add_source_to_campaign(campaign, source)

        with pytest.raises(CampaignSourceError):
            campaign_sources_service.add_source_to_campaign(campaign, source)


def test_conflicting_source_is_rejected(app, seeded_db):
    with app.app_context():
        from app.services import campaign_lifecycle

        source_a = sources_service.create_content_source(name="Mod A", kind="workshop_mod")
        source_b = sources_service.create_content_source(name="Mod B", kind="workshop_mod")
        sources_repo.set_relations(source_b, [("conflicts", source_a.id)])

        campaign = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        campaign_sources_service.add_source_to_campaign(campaign, source_a)

        with pytest.raises(CampaignSourceError):
            campaign_sources_service.add_source_to_campaign(campaign, source_b)


def test_remove_source_from_campaign_bumps_effective_catalog_version(app, seeded_db, db):
    with app.app_context():
        from app.services import campaign_lifecycle

        campaign = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        source = sources_repo.get_content_source_by_slug("xcom2-base")
        campaign_sources_service.add_source_to_campaign(campaign, source)

        version_after_add = db.session.get(Campaign, campaign.id).effective_catalog_version

        campaign_sources_service.remove_source_from_campaign(campaign, source)

        version_after_remove = db.session.get(Campaign, campaign.id).effective_catalog_version
        assert version_after_remove > version_after_add
        assert campaign_sources_repo.get_association(campaign.id, source.id) is None


def test_toggle_source_enabled(app, seeded_db):
    with app.app_context():
        from app.services import campaign_lifecycle

        campaign = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        source = sources_repo.get_content_source_by_slug("xcom2-base")
        campaign_sources_service.add_source_to_campaign(campaign, source)

        campaign_sources_service.set_source_enabled(campaign, source, False)
        association = campaign_sources_repo.get_association(campaign.id, source.id)
        assert association.enabled is False

        campaign_sources_service.set_source_enabled(campaign, source, True)
        association = campaign_sources_repo.get_association(campaign.id, source.id)
        assert association.enabled is True


def test_reorder_sources_updates_precedence(app, seeded_db):
    with app.app_context():
        from app.services import campaign_lifecycle

        campaign = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        base = sources_repo.get_content_source_by_slug("xcom2-base")
        wotc = sources_repo.get_content_source_by_slug("wotc")
        campaign_sources_service.add_source_to_campaign(campaign, base)
        campaign_sources_service.add_source_to_campaign(campaign, wotc)

        campaign_sources_service.reorder_sources(campaign, [wotc.id, base.id])

        wotc_assoc = campaign_sources_repo.get_association(campaign.id, wotc.id)
        base_assoc = campaign_sources_repo.get_association(campaign.id, base.id)
        assert wotc_assoc.precedence < base_assoc.precedence


def test_available_sources_to_add_excludes_attached(app, seeded_db):
    with app.app_context():
        from app.services import campaign_lifecycle

        campaign = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        base = sources_repo.get_content_source_by_slug("xcom2-base")
        campaign_sources_service.add_source_to_campaign(campaign, base)

        available = campaign_sources_repo.available_sources_to_add(campaign.id)
        assert base.id not in {s.id for s in available}
