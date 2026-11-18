"""Avenger Facility Planner service tests (ADR-0020): grid initialisation,
room-state editing, facility assignment, build lifecycle, ruleset filtering,
duplicate rules and prerequisite evaluation, against real curated packs and
small synthetic packs."""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from tests.avenger_helpers import (
    attach_source,
    facility,
    facility_id,
    import_real_packs,
    new_campaign,
    room_id,
    write_pack,
)


@pytest.fixture()
def real(app, seeded_db):
    with app.app_context():
        import_real_packs()
        yield


# ------------------------------------------------------------ initialisation


def test_new_campaign_gets_exactly_twelve_unique_unconfigured_cells(app, seeded_db):
    from app.repositories import avenger_cells as cells_repo

    campaign = new_campaign("A", None)
    cells = cells_repo.list_cells(campaign.id)

    assert len(cells) == 12
    assert len({c.coordinate for c in cells}) == 12
    assert {(c.grid_row, c.grid_column) for c in cells} == {(r, c) for r in range(1, 5) for c in range(1, 4)}
    assert all(c.room_state_object_id is None for c in cells)
    assert all(c.facility_object_id is None and c.build_state == "none" for c in cells)


def test_ensure_grid_is_idempotent_and_repairs_missing_cells(app, seeded_db):
    from app.extensions import db
    from app.models import CampaignAvengerCell
    from app.repositories import avenger_cells as cells_repo
    from app.services import avenger

    campaign = new_campaign("A", None)
    avenger.ensure_grid(campaign)
    avenger.ensure_grid(campaign)
    assert len(cells_repo.list_cells(campaign.id)) == 12

    CampaignAvengerCell.query.filter_by(campaign_id=campaign.id, coordinate="R3C2").delete()
    db.session.commit()
    assert len(cells_repo.list_cells(campaign.id)) == 11

    view = avenger.get_grid_view(campaign)  # lazily recreates a pre-existing campaign's grid
    assert len(view.cells) == 12
    assert [c.coordinate for c in view.cells][4] == "R2C2"


def test_database_rejects_duplicate_and_invalid_coordinates(app, seeded_db):
    from app.extensions import db
    from app.models import CampaignAvengerCell

    campaign = new_campaign("A", None)

    db.session.add(CampaignAvengerCell(campaign_id=campaign.id, coordinate="R1C1", grid_row=1, grid_column=1))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    db.session.add(CampaignAvengerCell(campaign_id=campaign.id, coordinate="R5C1", grid_row=5, grid_column=1))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    db.session.add(CampaignAvengerCell(campaign_id=campaign.id, coordinate="R1C2", grid_row=2, grid_column=1))
    with pytest.raises(IntegrityError):  # coordinate must agree with row/column
        db.session.commit()
    db.session.rollback()


def test_database_rejects_facility_without_build_state_and_vice_versa(app, real):
    from app.extensions import db
    from app.repositories import avenger_cells as cells_repo

    campaign = new_campaign("A")
    cell = cells_repo.get_cell(campaign.id, "R1C1")
    cell.build_state = "built"  # a build state with no facility
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    cell = cells_repo.get_cell(campaign.id, "R1C1")
    cell.facility_object_id = facility_id(campaign, "Laboratory")  # a facility with state 'none'
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_unconfigured_grid_view_has_no_room_states(app, real):
    from app.services import avenger

    view = avenger.get_grid_view(new_campaign("A"))

    assert not view.is_configured
    assert view.summary.configured_cells == 0
    assert view.summary.unconfigured_cells == 12
    assert all(cell.room is None and cell.status == "empty" for cell in view.cells)


# --------------------------------------------------------------- room states


def test_room_options_come_from_catalog_not_code(app, real):
    from app.services import avenger

    names = {o.name for o in avenger.list_room_options(new_campaign("A"))}
    assert names == {"Empty / Cleared", "Alien Debris", "Alien Machinery", "Exposed Power Coil"}


def test_room_options_are_extensible_by_a_mod_pack(app, real, tmp_path):
    from app.services import avenger
    from app.services.content_import import import_pack

    pack = write_pack(
        tmp_path,
        "rooms-mod",
        requires=["xcom2-base"],
        objects=[
            {
                "template_key": "RoomState_Flooded",
                "type": "avenger_room_state",
                "name": "Flooded Compartment",
                "attributes": {"clearable": True, "blocks_construction": True},
            }
        ],
    )
    import_pack(str(pack))
    with_mod = new_campaign("With mod")
    attach_source(with_mod, "rooms-mod")
    without_mod = new_campaign("Without mod")

    assert "Flooded Compartment" in {o.name for o in avenger.list_room_options(with_mod)}
    assert "Flooded Compartment" not in {o.name for o in avenger.list_room_options(without_mod)}

    avenger.set_room_state(with_mod, "R1C1", room_id(with_mod, "Flooded Compartment"))
    assert avenger.get_cell_view(with_mod, "R1C1").room.name == "Flooded Compartment"
    with pytest.raises(avenger.AvengerGridError):
        avenger.set_room_state(without_mod, "R1C1", room_id(with_mod, "Flooded Compartment"))


def test_set_room_state_edits_one_cell_only(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.set_room_state(campaign, "R4C2", room_id(campaign, "Exposed Power Coil"))

    view = avenger.get_grid_view(campaign)
    assert view.cell("R4C2").room.name == "Exposed Power Coil"
    assert view.cell("R4C2").room.blocks_construction
    assert sum(1 for c in view.cells if c.room is not None) == 1
    avenger.set_room_state(campaign, "R4C2", None)
    assert avenger.get_cell_view(campaign, "R4C2").room is None


def test_set_room_state_rejects_non_room_objects_and_bad_coordinates(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    with pytest.raises(avenger.AvengerGridError):
        avenger.set_room_state(campaign, "R1C1", facility_id(campaign, "Laboratory"))
    with pytest.raises(avenger.AvengerGridError):
        avenger.set_room_state(campaign, "R1C1", 999_999)
    with pytest.raises(avenger.AvengerGridError):
        avenger.set_room_state(campaign, "R9C9", room_id(campaign, "Alien Debris"))


def test_configure_layout_saves_whole_draft(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    debris, empty = room_id(campaign, "Alien Debris"), room_id(campaign, "Empty / Cleared")
    written = avenger.configure_layout(campaign, {"R1C1": debris, "R1C2": empty, "R4C3": debris})

    assert written == 3
    view = avenger.get_grid_view(campaign)
    assert view.summary.configured_cells == 3
    assert view.cell("R1C1").room.name == "Alien Debris"
    assert view.cell("R2C1").room is None


def test_configure_layout_is_atomic_when_any_entry_is_invalid(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    debris = room_id(campaign, "Alien Debris")
    with pytest.raises(avenger.AvengerGridError):
        avenger.configure_layout(campaign, {"R1C1": debris, "R1C2": facility_id(campaign, "Laboratory")})

    assert avenger.get_grid_view(campaign).summary.configured_cells == 0

    with pytest.raises(avenger.AvengerGridError):
        avenger.configure_layout(campaign, {"R1C1": debris, "R7C7": debris})
    assert avenger.get_grid_view(campaign).summary.configured_cells == 0


def test_configure_layout_leaves_unmentioned_cells_alone(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    debris, empty = room_id(campaign, "Alien Debris"), room_id(campaign, "Empty / Cleared")
    avenger.configure_layout(campaign, {"R1C1": debris, "R2C2": empty})
    avenger.configure_layout(campaign, {"R1C1": empty})

    view = avenger.get_grid_view(campaign)
    assert view.cell("R1C1").room.name == "Empty / Cleared"
    assert view.cell("R2C2").room.name == "Empty / Cleared"


def test_reset_layout_sets_every_cell_to_default_empty_and_keeps_plans(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.configure_layout(campaign, {"R1C1": room_id(campaign, "Alien Debris")})
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Laboratory"))

    avenger.reset_layout(campaign)

    view = avenger.get_grid_view(campaign)
    assert all(c.room.name == "Empty / Cleared" for c in view.cells)
    assert view.cell("R1C1").facility.name == "Laboratory"  # the plan survives a room reset

    avenger.reset_layout(campaign, clear_facilities=True)
    assert avenger.get_grid_view(campaign).summary.occupied_cells == 0


# ------------------------------------------------------ facility assignment


def test_facility_options_are_catalog_driven_and_ruleset_filtered(app, real):
    from app.services import avenger

    wotc = {o.name for o in avenger.list_facility_options(new_campaign("WotC", "wotc-standard"))}
    vanilla = {o.name for o in avenger.list_facility_options(new_campaign("Vanilla", None))}
    base_only = new_campaign("Base only", None)
    attach_source(base_only, "xcom2-base")
    vanilla = {o.name for o in avenger.list_facility_options(base_only)}

    assert "Advanced Warfare Center" in vanilla  # vanilla XCOM 2 only
    assert "Advanced Warfare Center" not in wotc  # removed by War of the Chosen via patch override
    assert {"Infirmary", "Training Center", "Resistance Ring"} <= wotc
    assert not ({"Infirmary", "Training Center", "Resistance Ring"} & vanilla)
    assert not avenger.list_facility_options(new_campaign("No sources", None))


def test_lwotc_campaign_resolves_overriding_facility_definitions(app, real):
    from app.models import CatalogObject
    from app.services import avenger

    campaign = new_campaign("LWotC", "lwotc")
    option = next(o for o in avenger.list_facility_options(campaign) if o.name == "Infirmary")
    assert CatalogObject.query.get(option.object_id).template_key == "Infirmary_LWOTC"

    base_infirmary = CatalogObject.query.filter_by(template_key="Infirmary").first()
    avenger.assign_facility(campaign, "R1C1", base_infirmary.id)  # base id normalises to the ruleset's winner
    assert avenger.get_cell_view(campaign, "R1C1").facility.object_id == option.object_id


def test_assign_facility_defaults_to_planned(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.assign_facility(campaign, "R2C3", facility_id(campaign, "Laboratory"))

    cell = avenger.get_cell_view(campaign, "R2C3")
    assert cell.facility.name == "Laboratory"
    assert cell.build_state == "planned"


def test_room_state_and_facility_and_build_state_are_independent(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.set_room_state(campaign, "R4C2", room_id(campaign, "Exposed Power Coil"))
    avenger.assign_facility(campaign, "R4C2", facility_id(campaign, "Power Relay"))
    avenger.set_build_state(campaign, "R4C2", "constructing")
    avenger.set_room_state(campaign, "R4C2", room_id(campaign, "Empty / Cleared"))

    cell = avenger.get_cell_view(campaign, "R4C2")
    assert (cell.room.name, cell.facility.name, cell.build_state) == (
        "Empty / Cleared", "Power Relay", "constructing",
    )  # fmt: skip


def test_planned_versus_built_lifecycle(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Workshop"))
    assert avenger.get_cell_view(campaign, "R1C1").status in ("planned_available", "planned_blocked")

    avenger.set_build_state(campaign, "R1C1", "constructing")
    assert avenger.get_cell_view(campaign, "R1C1").status == "constructing"
    avenger.set_build_state(campaign, "R1C1", "built")
    assert avenger.get_cell_view(campaign, "R1C1").status == "built"
    avenger.set_build_state(campaign, "R1C1", "planned")  # correcting a mis-click keeps the facility
    cell = avenger.get_cell_view(campaign, "R1C1")
    assert cell.facility.name == "Workshop" and cell.build_state == "planned"

    summary = avenger.get_grid_view(campaign).summary
    assert (summary.built, summary.constructing, summary.planned) == (0, 0, 1)


def test_clear_facility_returns_cell_to_no_facility(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Workshop"), build_state="built")
    avenger.clear_facility(campaign, "R1C1")

    cell = avenger.get_cell_view(campaign, "R1C1")
    assert cell.facility is None and cell.build_state == "none" and cell.status == "empty"


def test_set_build_state_requires_a_facility_and_a_valid_state(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    with pytest.raises(avenger.FacilityAssignmentError):
        avenger.set_build_state(campaign, "R1C1", "built")
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Workshop"))
    with pytest.raises(avenger.FacilityAssignmentError):
        avenger.set_build_state(campaign, "R1C1", "demolished")
    with pytest.raises(avenger.FacilityAssignmentError):
        avenger.set_build_state(campaign, "R1C1", "none")


def test_invalid_facility_assignments_are_rejected(app, real, tmp_path):
    from app.models import CatalogObject
    from app.services import avenger
    from app.services.content_import import import_pack

    campaign = new_campaign("WotC", "wotc-standard")
    non_facility = CatalogObject.query.filter_by(template_key="Supplies").first()
    awc = CatalogObject.query.filter_by(template_key="AdvancedWarfareCenter").first()

    with pytest.raises(avenger.FacilityAssignmentError):
        avenger.assign_facility(campaign, "R1C1", non_facility.id)  # not a facility
    with pytest.raises(avenger.FacilityAssignmentError):
        avenger.assign_facility(campaign, "R1C1", awc.id)  # removed in this ruleset
    with pytest.raises(avenger.FacilityAssignmentError):
        avenger.assign_facility(campaign, "R1C1", 987_654)  # does not exist
    with pytest.raises(avenger.FacilityAssignmentError):
        avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Workshop"), build_state="none")

    other = write_pack(tmp_path, "other-mod", requires=["xcom2-base"], objects=[facility("Other", "Other Facility")])
    import_pack(str(other))
    foreign = CatalogObject.query.filter_by(template_key="Other").first()
    with pytest.raises(avenger.FacilityAssignmentError):
        avenger.assign_facility(campaign, "R1C1", foreign.id)  # source not enabled for this campaign
    with pytest.raises(avenger.AvengerGridError):
        avenger.assign_facility(campaign, "R0C0", facility_id(campaign, "Workshop"))

    assert avenger.get_grid_view(campaign).summary.occupied_cells == 0


def test_facility_flagged_not_buildable_in_grid_is_excluded(app, real, tmp_path):
    from app.models import CatalogObject
    from app.services import avenger
    from app.services.content_import import import_pack

    pack = write_pack(
        tmp_path,
        "special",
        requires=["xcom2-base"],
        objects=[facility("Hidden", "Geoscape Only Facility", buildable_in_avenger_grid=False), facility("Shown", "Grid Facility")],
    )
    import_pack(str(pack))
    campaign = new_campaign("A")
    attach_source(campaign, "special")

    names = {o.name for o in avenger.list_facility_options(campaign)}
    assert "Grid Facility" in names and "Geoscape Only Facility" not in names
    with pytest.raises(avenger.FacilityAssignmentError):
        avenger.assign_facility(campaign, "R1C1", CatalogObject.query.filter_by(template_key="Hidden").first().id)


# ---------------------------------------------------------------- duplicates


def test_unique_facility_cannot_be_placed_twice(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    lab = facility_id(campaign, "Laboratory")
    avenger.assign_facility(campaign, "R1C1", lab)

    with pytest.raises(avenger.FacilityAssignmentError, match="only be built once"):
        avenger.assign_facility(campaign, "R1C2", lab)
    avenger.assign_facility(campaign, "R1C1", lab, build_state="built")  # same cell: just a state change
    assert avenger.get_grid_view(campaign).summary.occupied_cells == 1

    option = next(o for o in avenger.list_facility_options(campaign) if o.name == "Laboratory")
    assert option.duplicate_blocked and option.placed_at == ("R1C1",)
    own_cell_option = next(
        o for o in avenger.list_facility_options(campaign, for_coordinate="R1C1") if o.name == "Laboratory"
    )
    assert not own_cell_option.duplicate_blocked  # re-selecting it in its own cell is fine


def test_duplicable_facility_may_be_placed_many_times(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    relay = facility_id(campaign, "Power Relay")
    for coordinate in ("R1C1", "R2C2", "R4C3"):
        avenger.assign_facility(campaign, coordinate, relay)

    assert avenger.get_grid_view(campaign).summary.planned == 3


def test_ruleset_can_change_duplicate_rule_via_override(app, real, tmp_path):
    from app.models import CatalogObject
    from app.services import avenger
    from app.services.content_import import import_pack

    lab = CatalogObject.query.filter_by(template_key="Laboratory").first()
    assert lab is not None
    pack = write_pack(
        tmp_path,
        "multi-lab",
        requires=["xcom2-base"],
        objects=[
            {
                "template_key": "Laboratory_Multi",
                "type": "facility",
                "name": "Laboratory",
                "overrides": ["xcom2-base", "Laboratory"],
                "patch": {"attributes": {"allows_duplicates": True}},
            }
        ],
    )
    import_pack(str(pack))

    vanilla_rules = new_campaign("Vanilla rules", "wotc-standard")
    modded_rules = new_campaign("Modded rules", "wotc-standard")
    attach_source(modded_rules, "multi-lab")

    avenger.assign_facility(vanilla_rules, "R1C1", facility_id(vanilla_rules, "Laboratory"))
    with pytest.raises(avenger.FacilityAssignmentError):
        avenger.assign_facility(vanilla_rules, "R1C2", facility_id(vanilla_rules, "Laboratory"))

    avenger.assign_facility(modded_rules, "R1C1", facility_id(modded_rules, "Laboratory"))
    avenger.assign_facility(modded_rules, "R1C2", facility_id(modded_rules, "Laboratory"))  # allowed by active ruleset
    assert avenger.get_grid_view(modded_rules).summary.planned == 2


# ------------------------------------------------------------- prerequisites


def test_blocked_prerequisite_is_shown_and_plan_is_kept(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.assign_facility(campaign, "R2C2", facility_id(campaign, "Proving Ground"))

    cell = avenger.get_cell_view(campaign, "R2C2")
    assert cell.facility.name == "Proving Ground"  # planned despite being blocked
    assert cell.status == "planned_blocked"
    assert [b.text for b in cell.blockers] == ["ADVENT Officer Autopsy prerequisite not recorded as complete"]
    assert [name for _, name in cell.blockers[0].options] == ["ADVENT Officer Autopsy"]

    option = next(o for o in avenger.list_facility_options(campaign) if o.name == "Proving Ground")
    assert not option.prerequisites_met
    assert next(o for o in avenger.list_facility_options(campaign) if o.name == "Workshop").prerequisites_met


def test_recording_prerequisite_complete_unblocks_without_losing_plan(app, real):
    from app.models import CatalogObject
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.assign_facility(campaign, "R2C2", facility_id(campaign, "Proving Ground"))
    autopsy = CatalogObject.query.filter_by(template_key="AutopsyAdventOfficer").first()

    avenger.set_prerequisite_state(campaign, autopsy.id, completed=True)
    cell = avenger.get_cell_view(campaign, "R2C2")
    assert cell.status == "planned_available" and not cell.blockers and cell.facility.name == "Proving Ground"

    avenger.set_prerequisite_state(campaign, autopsy.id, completed=False)
    assert avenger.get_cell_view(campaign, "R2C2").status == "planned_blocked"


def test_constructing_or_built_facilities_are_never_shown_blocked(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.assign_facility(campaign, "R2C2", facility_id(campaign, "Proving Ground"), build_state="built")
    cell = avenger.get_cell_view(campaign, "R2C2")
    assert cell.status == "built" and not cell.has_warning


def test_room_blocker_applies_only_while_planned(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.set_room_state(campaign, "R1C2", room_id(campaign, "Alien Debris"))
    avenger.assign_facility(campaign, "R1C2", facility_id(campaign, "Workshop"))

    cell = avenger.get_cell_view(campaign, "R1C2")
    assert cell.room_blocker == "Alien Debris must be cleared before construction"
    assert cell.status == "planned_blocked"

    avenger.set_build_state(campaign, "R1C2", "constructing")
    assert avenger.get_cell_view(campaign, "R1C2").room_blocker is None


def test_empty_cleared_room_does_not_block(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.set_room_state(campaign, "R1C1", room_id(campaign, "Empty / Cleared"))
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Workshop"))
    assert avenger.get_cell_view(campaign, "R1C1").room_blocker is None


def test_set_prerequisite_state_rejects_objects_outside_the_campaign(app, real):
    from app.models import CatalogObject
    from app.services import avenger

    campaign = new_campaign("Base only", None)
    attach_source(campaign, "xcom2-base")
    infirmary = CatalogObject.query.filter_by(template_key="Infirmary").first()  # wotc object
    with pytest.raises(avenger.AvengerGridError):
        avenger.set_prerequisite_state(campaign, infirmary.id, completed=True)


def _prereq_pack(tmp_path):
    from app.services.content_import import import_pack

    objects = [
        {"template_key": "TechA", "type": "research_project", "name": "Tech A"},
        {"template_key": "TechB", "type": "research_project", "name": "Tech B"},
        {"template_key": "TechC", "type": "research_project", "name": "Tech C"},
        facility("Hub", "Hub"),
        facility("Annex", "Annex"),
        facility("Vault", "Vault"),
        facility("Silo", "Silo"),
    ]
    relationships = [
        {"type": "prerequisite", "from": ["prereqs", "TechA"], "to": ["prereqs", "Annex"], "group_key": "annex-path", "group_logic": "OR"},
        {"type": "prerequisite", "from": ["prereqs", "TechB"], "to": ["prereqs", "Annex"], "group_key": "annex-path", "group_logic": "OR"},
        {"type": "requires_facility", "from": ["prereqs", "Hub"], "to": ["prereqs", "Silo"]},
    ]
    groups = [
        {
            "target": ["prereqs", "Vault"],
            "relationship_type": "prerequisite",
            "root": {
                "logic": "AND",
                "members": [
                    ["prereqs", "TechA"],
                    {"logic": "OR", "members": [["prereqs", "TechB"], ["prereqs", "TechC"]]},
                ],
            },
        }
    ]
    import_pack(str(write_pack(tmp_path, "prereqs", objects=objects, relationships=relationships, prerequisite_groups=groups, requires=["xcom2-base"])))
    campaign = new_campaign("Prereq campaign", None)
    attach_source(campaign, "xcom2-base")
    attach_source(campaign, "prereqs")
    return campaign


def test_or_group_blocker_names_all_alternatives_and_clears_with_any_one(app, real, tmp_path):
    from app.models import CatalogObject
    from app.services import avenger

    campaign = _prereq_pack(tmp_path)
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Annex"))

    blocker = avenger.get_cell_view(campaign, "R1C1").blockers[0]
    assert "Tech A" in blocker.text and "Tech B" in blocker.text and " or " in blocker.text
    assert {name for _, name in blocker.options} == {"Tech A", "Tech B"}

    tech_b = CatalogObject.query.filter_by(template_key="TechB").first()
    avenger.set_prerequisite_state(campaign, tech_b.id, completed=True)
    assert not avenger.get_cell_view(campaign, "R1C1").blockers


def test_compound_nested_group_is_evaluated(app, real, tmp_path):
    from app.models import CatalogObject
    from app.services import avenger

    campaign = _prereq_pack(tmp_path)
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Vault"))
    tech = {k: CatalogObject.query.filter_by(template_key=k).first().id for k in ("TechA", "TechB", "TechC")}

    assert len(avenger.get_cell_view(campaign, "R1C1").blockers) == 2  # A, and (B or C)
    avenger.set_prerequisite_state(campaign, tech["TechA"], completed=True)
    assert len(avenger.get_cell_view(campaign, "R1C1").blockers) == 1
    avenger.set_prerequisite_state(campaign, tech["TechC"], completed=True)
    assert not avenger.get_cell_view(campaign, "R1C1").blockers


def test_facility_requirement_is_satisfied_by_a_built_facility_in_the_grid(app, real, tmp_path):
    from app.services import avenger

    campaign = _prereq_pack(tmp_path)
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Silo"))
    blocker = avenger.get_cell_view(campaign, "R1C1").blockers[0]
    assert "Hub facility not recorded as complete" in blocker.text
    assert blocker.options == ()  # a facility requirement is met by building it, not by ticking a box

    avenger.assign_facility(campaign, "R2C1", facility_id(campaign, "Hub"), build_state="planned")
    assert avenger.get_cell_view(campaign, "R1C1").blockers  # planned is not enough
    avenger.set_build_state(campaign, "R2C1", "constructing")
    assert avenger.get_cell_view(campaign, "R1C1").blockers
    avenger.set_build_state(campaign, "R2C1", "built")
    assert not avenger.get_cell_view(campaign, "R1C1").blockers


# ------------------------------------------------------------------ summary


def test_summary_counts_and_blocked_plans(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.configure_layout(
        campaign,
        {"R1C1": room_id(campaign, "Empty / Cleared"), "R1C2": room_id(campaign, "Alien Debris"), "R1C3": room_id(campaign, "Alien Machinery")},
    )
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Laboratory"), build_state="built")
    avenger.assign_facility(campaign, "R2C1", facility_id(campaign, "Workshop"), build_state="constructing")
    avenger.assign_facility(campaign, "R2C2", facility_id(campaign, "Proving Ground"))

    s = avenger.get_grid_view(campaign).summary
    assert (s.built, s.constructing, s.planned, s.planned_blocked) == (1, 1, 1, 1)
    assert s.occupied_cells == 3 and s.free_cells == 9
    assert s.configured_cells == 3 and s.unconfigured_cells == 9
    assert s.hazard_cells == 2
    assert [(coordinate, name) for coordinate, name, _ in s.blocked_plans] == [("R2C2", "Proving Ground")]


def test_power_summary_uses_catalog_numbers_and_does_not_invent_totals(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Laboratory"), build_state="built")  # draws 3
    avenger.assign_facility(campaign, "R1C2", facility_id(campaign, "Power Relay"), build_state="built")  # generates 3
    avenger.assign_facility(campaign, "R1C3", facility_id(campaign, "Workshop"))  # planned, draws 1

    power = avenger.get_grid_view(campaign).summary.power
    assert (power.built_draw, power.built_generated) == (3, 3)
    assert (power.pending_draw, power.pending_generated) == (1, 0)
    assert power.facilities_without_power_data == 0
    assert not hasattr(power, "net")  # no fabricated surplus/shortfall


def test_missing_catalog_data_is_reported_not_guessed(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Shadow Chamber"))  # build time not in catalog
    option = next(o for o in avenger.list_facility_options(campaign) if o.name == "Shadow Chamber")
    assert option.build_days is None


def test_facility_detail_includes_cost_power_staffing_prereqs(app, real):
    from app.services import avenger

    campaign = new_campaign("A")
    detail = avenger.get_facility_detail(campaign, facility_id(campaign, "Proving Ground"))

    assert detail.option.build_cost_supplies == 125
    assert detail.option.build_days == 7
    assert detail.option.power_cost == 3
    assert [p.name for p in detail.prerequisite_leaves] == ["ADVENT Officer Autopsy"]
    with pytest.raises(avenger.AvengerGridError):
        avenger.get_facility_detail(new_campaign("Base", None), facility_id(campaign, "Proving Ground"))
