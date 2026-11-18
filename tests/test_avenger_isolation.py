"""Campaign isolation for the Avenger planner (ADR-0010, ADR-0020)."""
from __future__ import annotations

import pytest

from tests.avenger_helpers import facility_id, import_real_packs, new_campaign, room_id


@pytest.fixture()
def real(app, seeded_db):
    with app.app_context():
        import_real_packs()
        yield


def _layout_a(campaign):
    from app.services import avenger

    avenger.configure_layout(
        campaign,
        {"R1C1": room_id(campaign, "Alien Debris"), "R4C2": room_id(campaign, "Exposed Power Coil")},
    )
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Laboratory"), build_state="built")
    avenger.assign_facility(campaign, "R4C2", facility_id(campaign, "Power Relay"))


def _layout_b(campaign):
    from app.services import avenger

    avenger.configure_layout(
        campaign,
        {"R1C1": room_id(campaign, "Empty / Cleared"), "R3C3": room_id(campaign, "Alien Machinery")},
    )
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Workshop"), build_state="constructing")
    avenger.assign_facility(campaign, "R2C2", facility_id(campaign, "Laboratory"))  # same catalog facility, different plan


def test_two_campaigns_hold_completely_different_layouts(app, real):
    from app.services import avenger

    a, b = new_campaign("A"), new_campaign("B")
    _layout_a(a)
    _layout_b(b)

    view_a, view_b = avenger.get_grid_view(a), avenger.get_grid_view(b)
    assert view_a.cell("R1C1").room.name == "Alien Debris"
    assert view_b.cell("R1C1").room.name == "Empty / Cleared"
    assert (view_a.cell("R1C1").facility.name, view_a.cell("R1C1").build_state) == ("Laboratory", "built")
    assert (view_b.cell("R1C1").facility.name, view_b.cell("R1C1").build_state) == ("Workshop", "constructing")
    assert view_a.cell("R4C2").facility.name == "Power Relay" and view_b.cell("R4C2").facility is None
    assert view_b.cell("R2C2").facility.name == "Laboratory" and view_a.cell("R2C2").facility is None
    assert view_a.cell("R3C3").room is None and view_b.cell("R3C3").room.name == "Alien Machinery"


def test_unique_facility_rule_is_per_campaign(app, real):
    from app.services import avenger

    a, b = new_campaign("A"), new_campaign("B")
    avenger.assign_facility(a, "R1C1", facility_id(a, "Laboratory"))
    avenger.assign_facility(b, "R3C3", facility_id(b, "Laboratory"))  # not blocked by campaign A's Laboratory

    assert avenger.get_cell_view(b, "R3C3").facility.name == "Laboratory"


def test_mutating_one_campaign_never_changes_another(app, real):
    from app.services import avenger

    a, b = new_campaign("A"), new_campaign("B")
    _layout_a(a)
    _layout_b(b)
    before = [(c.coordinate, c.room and c.room.name, c.facility and c.facility.name, c.build_state) for c in avenger.get_grid_view(b).cells]

    avenger.reset_layout(a, clear_facilities=True)
    avenger.assign_facility(a, "R3C3", facility_id(a, "Workshop"), build_state="built")
    avenger.clear_facility(a, "R1C1")

    after = [(c.coordinate, c.room and c.room.name, c.facility and c.facility.name, c.build_state) for c in avenger.get_grid_view(b).cells]
    assert before == after


def test_prerequisite_progress_is_campaign_scoped(app, real):
    from app.models import CatalogObject
    from app.services import avenger

    a, b = new_campaign("A"), new_campaign("B")
    for campaign in (a, b):
        avenger.assign_facility(campaign, "R2C2", facility_id(campaign, "Proving Ground"))
    autopsy = CatalogObject.query.filter_by(template_key="AutopsyAdventOfficer").first()

    avenger.set_prerequisite_state(a, autopsy.id, completed=True)

    assert avenger.get_cell_view(a, "R2C2").status == "planned_available"
    assert avenger.get_cell_view(b, "R2C2").status == "planned_blocked"


def test_deleting_campaign_a_leaves_campaign_b_and_the_catalog_untouched(app, real):
    from app.extensions import db
    from app.models import CampaignAvengerCell, CatalogObject
    from app.services import avenger, campaign_lifecycle

    a, b = new_campaign("A"), new_campaign("B")
    _layout_a(a)
    _layout_b(b)
    a_id, b_id = a.id, b.id
    facility_count = CatalogObject.query.count()
    b_cells = [
        (c.coordinate, c.room_state_object_id, c.facility_object_id, c.build_state)
        for c in CampaignAvengerCell.query.filter_by(campaign_id=b_id).order_by(CampaignAvengerCell.coordinate)
    ]

    campaign_lifecycle.delete_campaign(a)
    db.session.expire_all()

    assert CampaignAvengerCell.query.filter_by(campaign_id=a_id).count() == 0
    assert CampaignAvengerCell.query.filter_by(campaign_id=b_id).count() == 12
    assert CatalogObject.query.count() == facility_count
    assert b_cells == [
        (c.coordinate, c.room_state_object_id, c.facility_object_id, c.build_state)
        for c in CampaignAvengerCell.query.filter_by(campaign_id=b_id).order_by(CampaignAvengerCell.coordinate)
    ]
    assert avenger.get_cell_view(b, "R1C1").facility.name == "Workshop"
    assert len(avenger.list_facility_options(b)) > 0  # catalog facility definitions remain global


def test_editing_the_global_facility_definition_does_not_overwrite_plans(app, real, tmp_path):
    """Re-importing a pack with changed facility numbers updates the catalog;
    each campaign keeps its own assignment and build state."""
    import json

    from app.models import CatalogObject
    from app.services import avenger
    from app.services.content_import import import_pack

    a = new_campaign("A")
    avenger.assign_facility(a, "R1C1", facility_id(a, "Workshop"), build_state="constructing")
    avenger.assign_facility(a, "R1C2", facility_id(a, "Laboratory"))
    workshop = CatalogObject.query.filter_by(template_key="Workshop").first()
    original_id = workshop.id

    pack_dir = tmp_path / "xcom2-base-edit"
    import shutil

    from app.cli import _pack_dir_for

    shutil.copytree(_pack_dir_for("xcom2-base"), pack_dir)
    objects = json.loads((pack_dir / "objects.json").read_text())
    for obj in objects:
        if obj["template_key"] == "Workshop":
            obj["attributes"]["build_cost_supplies"] = 999
            obj["name"] = "Workshop (Revised)"
    (pack_dir / "objects.json").write_text(json.dumps(objects))
    result = import_pack(str(pack_dir))
    assert result.objects_updated >= 1

    cell = avenger.get_cell_view(a, "R1C1")
    assert cell.facility.object_id == original_id  # same catalog row, updated definition
    assert cell.facility.name == "Workshop (Revised)"
    assert cell.facility.build_cost_supplies == 999
    assert cell.build_state == "constructing"  # campaign choice untouched
    assert avenger.get_cell_view(a, "R1C2").facility.name == "Laboratory"


def test_referenced_catalog_objects_are_retired_not_deleted(app, real):
    from app.models import CatalogObject
    from app.services import avenger
    from app.services import catalog_objects as objects_service

    a = new_campaign("A")
    avenger.assign_facility(a, "R1C1", facility_id(a, "Workshop"))
    workshop = CatalogObject.query.filter_by(template_key="Workshop").first()

    assert objects_service.retire_or_delete(workshop) == "retired"
    assert CatalogObject.query.filter_by(template_key="Workshop").first() is not None
    cell = avenger.get_cell_view(a, "R1C1")
    assert cell.facility.name == "Workshop"  # the campaign's plan is still readable


def test_removing_a_source_flags_plan_instead_of_dropping_it(app, real):
    from app.repositories import content_sources as sources_repo
    from app.services import avenger
    from app.services import campaign_sources as campaign_sources_service

    campaign = new_campaign("A", "wotc-standard")
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Infirmary"))
    wotc = sources_repo.get_content_source_by_slug("wotc")

    campaign_sources_service.set_source_enabled(campaign, wotc, False)

    cell = avenger.get_cell_view(campaign, "R1C1")
    assert cell.facility.name == "Infirmary"  # not destroyed
    assert not cell.facility.is_available
    assert any("content sources" in b.text for b in cell.blockers)
    assert cell.status == "planned_blocked"
