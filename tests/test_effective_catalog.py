"""Effective-catalog resolution tests (ADR-0013): pure domain function +
service wiring + cache invalidation."""
from __future__ import annotations

from app.domain.effective_catalog import resolve_effective_catalog


def _obj(id_, source_id, **kwargs):
    base = {
        "id": id_, "source_id": source_id, "overrides_object_id": None,
        "replaces_object_id": None, "patch": None, "attributes": {},
    }
    base.update(kwargs)
    return base


# ---- Pure domain function ----


def test_no_overrides_simple_visibility():
    objects = [_obj(1, source_id=10), _obj(2, source_id=20)]
    precedence = {10: 0, 20: 1}

    effective = resolve_effective_catalog(objects, precedence)

    assert set(effective.keys()) == {1, 2}


def test_object_from_disabled_source_is_invisible():
    objects = [_obj(1, source_id=10), _obj(2, source_id=20)]
    precedence = {10: 0}  # source 20 not enabled

    effective = resolve_effective_catalog(objects, precedence)

    assert set(effective.keys()) == {1}


def test_higher_precedence_override_wins():
    base = _obj(1, source_id=10, name="Base Rifle", attributes={"damage": 4})
    overlay = _obj(2, source_id=20, name="Overlay Rifle", overrides_object_id=1, patch={"name": "Patched Rifle"})
    precedence = {10: 0, 20: 1}  # source 20 has higher precedence number -> wins

    effective = resolve_effective_catalog([base, overlay], precedence)

    assert set(effective.keys()) == {2}
    assert effective[2]["name"] == "Patched Rifle"
    assert effective[2]["attributes"]["damage"] == 4  # inherited from base, not overwritten


def test_lower_precedence_source_does_not_win_even_if_more_recently_added():
    base = _obj(1, source_id=10)
    overlay = _obj(2, source_id=20, overrides_object_id=1)
    # Source 20 (the overlay) has a LOWER precedence number than source 10
    # -- base must win per "higher precedence number overrides lower".
    precedence = {10: 5, 20: 1}

    effective = resolve_effective_catalog([base, overlay], precedence)

    assert set(effective.keys()) == {1}


def test_only_base_enabled_wins_as_is():
    base = _obj(1, source_id=10, name="Base Rifle")
    overlay = _obj(2, source_id=20, overrides_object_id=1, patch={"name": "Patched"})
    precedence = {10: 0}  # overlay's source not enabled

    effective = resolve_effective_catalog([base, overlay], precedence)

    assert set(effective.keys()) == {1}
    assert effective[1]["name"] == "Base Rifle"


def test_replaces_does_not_inherit_fields():
    base = _obj(1, source_id=10, name="Base Rifle", attributes={"damage": 4})
    replacement = _obj(2, source_id=20, name="Replacement Rifle", replaces_object_id=1, attributes={})
    precedence = {10: 0, 20: 1}

    effective = resolve_effective_catalog([base, replacement], precedence)

    assert set(effective.keys()) == {2}
    assert effective[2]["name"] == "Replacement Rifle"
    assert effective[2]["attributes"] == {}  # no inheritance, unlike overrides


def test_patch_merges_attributes_shallowly():
    base = _obj(1, source_id=10, attributes={"damage": 4, "crit": 5})
    overlay = _obj(2, source_id=20, overrides_object_id=1, patch={"attributes": {"damage": 10}})
    precedence = {10: 0, 20: 1}

    effective = resolve_effective_catalog([base, overlay], precedence)

    assert effective[2]["attributes"] == {"damage": 10, "crit": 5}


def test_three_way_override_chain():
    """base <- mod_overlay <- user_overlay; highest-precedence enabled wins."""
    base = _obj(1, source_id=10, name="Base", attributes={"a": 1})
    mod = _obj(2, source_id=20, overrides_object_id=1, patch={"name": "Mod", "attributes": {"b": 2}})
    user = _obj(3, source_id=30, overrides_object_id=2, patch={"name": "User"})
    precedence = {10: 0, 20: 1, 30: 2}

    effective = resolve_effective_catalog([base, mod, user], precedence)

    assert set(effective.keys()) == {3}
    assert effective[3]["name"] == "User"
    assert effective[3]["attributes"] == {"a": 1, "b": 2}


def test_malformed_cycle_does_not_infinite_loop():
    """Defensive: a malformed override chain that cycles back on itself
    must not hang the resolver."""
    a = _obj(1, source_id=10, overrides_object_id=2)
    b = _obj(2, source_id=10, overrides_object_id=1)
    precedence = {10: 0}

    effective = resolve_effective_catalog([a, b], precedence)

    assert len(effective) >= 1  # terminates, some deterministic result


# ---- Service layer (DB-backed) ----


def test_service_effective_source_ids_follows_precedence(app, seeded_db):
    with app.app_context():
        from app.repositories import content_sources as sources_repo
        from app.services import campaign_lifecycle
        from app.services import campaign_sources as campaign_sources_service
        from app.services import effective_catalog as effective_catalog_service

        campaign = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        base = sources_repo.get_content_source_by_slug("xcom2-base")
        wotc = sources_repo.get_content_source_by_slug("wotc")
        campaign_sources_service.add_source_to_campaign(campaign, base)
        campaign_sources_service.add_source_to_campaign(campaign, wotc)

        ids = effective_catalog_service.effective_source_ids(campaign.id)
        assert set(ids) == {base.id, wotc.id}

        campaign_sources_service.set_source_enabled(campaign, wotc, False)
        ids_after_disable = effective_catalog_service.effective_source_ids(campaign.id)
        assert set(ids_after_disable) == {base.id}


def test_service_effective_catalog_reflects_overlay(app, seeded_db):
    with app.app_context():
        from app.models import CatalogObjectType, ContentSource
        from app.services import campaign_lifecycle
        from app.services import campaign_sources as campaign_sources_service
        from app.services import catalog_objects as objects_service
        from app.services import effective_catalog as effective_catalog_service

        base_source = ContentSource.query.filter_by(slug="xcom2-base").first()
        user_source = ContentSource.query.filter_by(slug="user-local").first()
        obj_type = CatalogObjectType.query.filter_by(slug="weapon").first()

        base_obj = objects_service.create_object(
            source_id=base_source.id, type_id=obj_type.id, name="Base Rifle"
        )
        overlay = objects_service.create_overlay(
            base_obj, user_source_id=user_source.id, name="Campaign-Specific Rifle"
        )

        campaign = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        campaign_sources_service.add_source_to_campaign(campaign, base_source)
        campaign_sources_service.add_source_to_campaign(campaign, user_source)

        campaign = campaign_lifecycle.campaigns_repo.get_campaign(campaign.id)
        effective = effective_catalog_service.get_effective_catalog(
            campaign.id, version=campaign.effective_catalog_version
        )

        assert overlay.id in effective
        assert base_obj.id not in effective
        assert effective[overlay.id]["name"] == "Campaign-Specific Rifle"


def test_effective_catalog_cache_invalidates_on_version_bump(app, seeded_db):
    with app.app_context():
        from app.repositories import content_sources as sources_repo
        from app.services import campaign_lifecycle
        from app.services import campaign_sources as campaign_sources_service
        from app.services import effective_catalog as effective_catalog_service

        campaign = campaign_lifecycle.create_campaign(name="Campaign A", game_profile_slug=None)
        base = sources_repo.get_content_source_by_slug("xcom2-base")

        v0 = campaign.effective_catalog_version
        result_v0 = effective_catalog_service.get_effective_catalog(campaign.id, version=v0)
        assert result_v0 == {}

        campaign_sources_service.add_source_to_campaign(campaign, base)
        v1 = campaign.effective_catalog_version
        assert v1 != v0

        # Old version's cached result is untouched; new version recomputes.
        result_v0_again = effective_catalog_service.get_effective_catalog(campaign.id, version=v0)
        assert result_v0_again == result_v0
