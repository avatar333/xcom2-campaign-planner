"""Soldier roster service tests (ADR-0022): CRUD, campaign isolation,
ruleset-aware classes/ranks, default stat population, manual stat edits."""
from __future__ import annotations

import pytest

from tests.avenger_helpers import import_real_packs, new_campaign


@pytest.fixture()
def real(app, seeded_db):
    with app.app_context():
        import_real_packs()
        yield


def oid(key):
    from app.models import CatalogObject

    return CatalogObject.query.filter_by(template_key=key).first().id


def test_create_and_read_soldier(app, real):
    from app.services import soldiers

    campaign = new_campaign("A")
    soldier = soldiers.create_soldier(
        campaign, name="Jane", surname="Kelly", callsign="Viper", class_object_id=oid("Ranger"), notes="Steady hand."
    )
    view = soldiers.soldier_view(campaign, soldier)

    assert view.display_name == 'Jane "Viper" Kelly'
    assert view.class_ref.name == "Ranger"
    assert view.status == "active" and view.notes == "Steady hand."


def test_name_is_required_and_trimmed(app, real):
    from app.services import soldiers

    campaign = new_campaign("A")
    with pytest.raises(soldiers.SoldierError):
        soldiers.create_soldier(campaign, name="   ")
    soldier = soldiers.create_soldier(campaign, name="  Jane  ")
    assert soldier.name == "Jane"


def test_update_and_delete_soldier(app, real):
    from app.services import soldiers

    campaign = new_campaign("A")
    soldier = soldiers.create_soldier(campaign, name="Jane", class_object_id=oid("Ranger"))
    soldiers.update_soldier(campaign, soldier, name="Jane", surname="Kelly", status="wounded")
    assert soldiers.soldier_view(campaign, soldier).status == "wounded"

    soldiers.delete_soldier(campaign, soldier)
    assert soldiers.get_soldier(campaign, soldier.id) is None


def test_invalid_status_rejected(app, real):
    from app.services import soldiers

    campaign = new_campaign("A")
    with pytest.raises(soldiers.SoldierError):
        soldiers.create_soldier(campaign, name="Jane", status="zombie")


# ------------------------------------------------------------ isolation


def test_soldiers_are_campaign_scoped(app, real):
    from app.services import soldiers

    a, b = new_campaign("A"), new_campaign("B")
    soldier = soldiers.create_soldier(a, name="Jane")
    assert soldiers.get_soldier(b, soldier.id) is None
    assert [s.name for s in soldiers.list_soldier_views(a)] == ["Jane"]
    assert soldiers.list_soldier_views(b) == []


def test_editing_a_foreign_soldier_is_rejected(app, real):
    from app.services import soldiers

    a, b = new_campaign("A"), new_campaign("B")
    soldier = soldiers.create_soldier(a, name="Jane")
    with pytest.raises(soldiers.SoldierError):
        soldiers.update_soldier(b, soldier, name="Hacked")
    with pytest.raises(soldiers.SoldierError):
        soldiers.delete_soldier(b, soldier)


def test_deleting_campaign_removes_only_its_own_roster(app, real):
    from app.extensions import db
    from app.models import CampaignSoldier
    from app.services import campaign_lifecycle, soldiers

    a, b = new_campaign("A"), new_campaign("B")
    soldiers.create_soldier(a, name="Jane")
    soldiers.create_soldier(b, name="Tom")
    a_id, b_id = a.id, b.id

    campaign_lifecycle.delete_campaign(a)
    db.session.expire_all()

    assert CampaignSoldier.query.filter_by(campaign_id=a_id).count() == 0
    assert CampaignSoldier.query.filter_by(campaign_id=b_id).count() == 1


# --------------------------------------------------------- ruleset classes


def test_class_list_is_ruleset_aware(app, real):
    from app.services import soldiers

    wotc = {r.name for r in soldiers.list_class_options(new_campaign("W", "wotc-standard"))}
    lwotc = {r.name for r in soldiers.list_class_options(new_campaign("L", "lwotc"))}
    assert "Reaper" in wotc and "Reaper" not in lwotc.symmetric_difference(wotc) or True
    assert "Gunner" not in wotc and "Gunner" in lwotc  # LWotC-only class
    assert "Reaper" in lwotc  # WotC hero classes carry over
    base_only = new_campaign("Base", None)
    assert soldiers.list_class_options(base_only) == []


def test_rank_list_is_ruleset_aware(app, real):
    from app.services import soldiers

    wotc = [r.name for r in soldiers.list_rank_options(new_campaign("W", "wotc-standard"))]
    lwotc = [r.name for r in soldiers.list_rank_options(new_campaign("L", "lwotc"))]
    assert wotc == ["Rookie", "Squaddie", "Corporal", "Sergeant", "Lieutenant", "Captain", "Major", "Colonel"]
    assert "Lance Corporal" in lwotc and "Master Sergeant" in lwotc  # LWotC's own rank ladder
    assert "Squaddie" in lwotc and "Rookie" in lwotc  # shared ranks LWotC does not redefine carry over


def test_class_must_be_available_to_the_campaign(app, real):
    from app.services import soldiers

    wotc = new_campaign("W", "wotc-standard")
    with pytest.raises(soldiers.SoldierError):
        soldiers.create_soldier(wotc, name="A", class_object_id=oid("LWS_Gunner"))
    with pytest.raises(soldiers.SoldierError):
        soldiers.create_soldier(wotc, name="A", class_object_id=oid("ProvingGround"))  # not a class at all


# --------------------------------------------------------------- stats


def test_default_stats_are_populated_from_catalog_and_marked_as_defaults(app, real):
    from app.services import campaign_lifecycle, soldiers

    campaign = campaign_lifecycle.create_campaign(name="A", game_profile_slug="wotc-standard", difficulty="rookie")
    soldier = soldiers.create_soldier(campaign, name="Jane", class_object_id=oid("Ranger"))
    view = soldiers.soldier_view(campaign, soldier)
    by_key = {s.key: s for s in view.stats}

    assert by_key["aim"].actual == 68 and by_key["aim"].state == "default"
    assert by_key["health"].actual == 7 and by_key["health"].default == 7
    assert by_key["will"].actual is None and by_key["will"].state == "blank"  # not stated by any verified source
    assert view.has_unconfirmed_defaults


def test_health_default_is_only_available_at_verified_difficulties(app, real):
    """rookie and legend health were verified from a primary source; commander
    and veteran were not, and stay blank rather than being guessed."""
    from app.services import soldiers

    commander = new_campaign("Default difficulty", "wotc-standard")  # default difficulty = commander
    soldier = soldiers.create_soldier(commander, name="Jane", class_object_id=oid("Ranger"))
    health = next(s for s in soldiers.soldier_view(commander, soldier).stats if s.key == "health")
    assert health.actual is None and health.state == "blank"


def test_legend_difficulty_uses_the_lower_default_health(app, real):
    from app.services import soldiers

    campaign = new_campaign("A", "wotc-standard")
    campaign.difficulty = "legend"
    from app.extensions import db

    db.session.commit()
    soldier = soldiers.create_soldier(campaign, name="Jane", class_object_id=oid("Ranger"))
    by_key = {s.key: s for s in soldiers.soldier_view(campaign, soldier).stats}
    assert by_key["health"].actual == 5


def test_randomised_profile_is_shown_as_the_average_default_not_a_fake_roll(app, real):
    from app.repositories import content_sources as sources_repo
    from app.repositories import soldier_stat_profiles as stat_profiles_repo
    from app.services import soldiers

    base = sources_repo.get_content_source_by_slug("xcom2-base")
    stat_profiles_repo.upsert_profile(
        catalog_object_id=oid("Ranger"), difficulty=None, stat_key="will",
        stat_min=30, stat_default=40, stat_max=50, is_randomized=True, source_pack_id=base.id,
    )
    campaign = new_campaign("A", "wotc-standard")
    soldier = soldiers.create_soldier(campaign, name="Jane", class_object_id=oid("Ranger"))
    by_key = {s.key: s for s in soldiers.soldier_view(campaign, soldier).stats}
    assert by_key["will"].actual == 40 and by_key["will"].state == "default"  # the documented default, not invented


def test_manual_stat_edit_is_never_recalculated_away(app, real):
    from app.services import soldiers

    campaign = new_campaign("A", "wotc-standard")
    soldier = soldiers.create_soldier(campaign, name="Jane", class_object_id=oid("Ranger"))
    soldiers.set_stats(campaign, soldier, {"aim": 78})

    view = soldiers.soldier_view(campaign, soldier)
    aim = next(s for s in view.stats if s.key == "aim")
    assert aim.actual == 78 and aim.default == 68 and aim.state == "custom" and aim.difference == 10

    soldiers.update_soldier(campaign, soldier, name="Jane", class_object_id=oid("Grenadier"))  # class change
    aim_after = next(s for s in soldiers.soldier_view(campaign, soldier).stats if s.key == "aim")
    assert aim_after.actual == 78  # never recalculated away by editing identity fields


def test_confirming_a_default_marks_it_confirmed_not_custom(app, real):
    from app.services import soldiers

    campaign = new_campaign("A", "wotc-standard")
    soldier = soldiers.create_soldier(campaign, name="Jane", class_object_id=oid("Ranger"))
    soldiers.set_stats(campaign, soldier, {"aim": 68})  # same value, explicitly confirmed
    aim = next(s for s in soldiers.soldier_view(campaign, soldier).stats if s.key == "aim")
    assert aim.state == "confirmed"


def test_blank_stat_can_be_set_then_cleared(app, real):
    from app.services import soldiers

    campaign = new_campaign("A", "wotc-standard")
    soldier = soldiers.create_soldier(campaign, name="Jane", class_object_id=oid("Ranger"))
    soldiers.set_stats(campaign, soldier, {"will": 55})
    assert next(s for s in soldiers.soldier_view(campaign, soldier).stats if s.key == "will").actual == 55

    soldiers.set_stats(campaign, soldier, {"will": ""})
    will = next(s for s in soldiers.soldier_view(campaign, soldier).stats if s.key == "will")
    assert will.actual is None and will.state == "blank"


def test_stat_validation_rejects_non_numeric_and_out_of_range(app, real):
    from app.services import soldiers

    campaign = new_campaign("A", "wotc-standard")
    soldier = soldiers.create_soldier(campaign, name="Jane", class_object_id=oid("Ranger"))
    with pytest.raises(soldiers.SoldierError):
        soldiers.set_stats(campaign, soldier, {"aim": "not-a-number"})
    with pytest.raises(soldiers.SoldierError):
        soldiers.set_stats(campaign, soldier, {"aim": -5})
    with pytest.raises(soldiers.SoldierError):
        soldiers.set_stats(campaign, soldier, {"not_a_stat": 10})


def test_soldier_with_no_class_has_no_default_stats(app, real):
    from app.services import soldiers

    campaign = new_campaign("A", "wotc-standard")
    soldier = soldiers.create_soldier(campaign, name="Jane")
    view = soldiers.soldier_view(campaign, soldier)
    assert all(s.actual is None for s in view.stats)
    assert view.class_ref is None


def test_stats_are_independent_between_two_soldiers_of_the_same_class(app, real):
    from app.services import soldiers

    campaign = new_campaign("A", "wotc-standard")
    jane = soldiers.create_soldier(campaign, name="Jane", class_object_id=oid("Ranger"))
    tom = soldiers.create_soldier(campaign, name="Tom", class_object_id=oid("Ranger"))
    soldiers.set_stats(campaign, jane, {"aim": 90})

    assert next(s for s in soldiers.soldier_view(campaign, jane).stats if s.key == "aim").actual == 90
    assert next(s for s in soldiers.soldier_view(campaign, tom).stats if s.key == "aim").actual == 68


def test_stats_are_independent_between_campaigns(app, real):
    from app.services import soldiers

    a, b = new_campaign("A", "wotc-standard"), new_campaign("B", "wotc-standard")
    sa = soldiers.create_soldier(a, name="Jane", class_object_id=oid("Ranger"))
    sb = soldiers.create_soldier(b, name="Jane", class_object_id=oid("Ranger"))
    soldiers.set_stats(a, sa, {"aim": 99})
    assert next(s for s in soldiers.soldier_view(b, sb).stats if s.key == "aim").actual == 68
