"""Pure-domain tests: grid geometry, build-state vocabulary, requirement trees."""
from __future__ import annotations

import pytest

from app.domain.avenger_grid import (
    ALL_COORDINATES,
    GRID_COLUMNS,
    GRID_ROWS,
    STATUS_BUILT,
    STATUS_CONSTRUCTING,
    STATUS_EMPTY,
    STATUS_PLANNED_AVAILABLE,
    STATUS_PLANNED_BLOCKED,
    all_cells,
    display_status,
    make_coordinate,
    parse_coordinate,
)
from app.domain.requirements import RequirementGroup, RequirementLeaf, evaluate_requirements


def test_grid_is_three_columns_by_four_rows():
    assert (GRID_COLUMNS, GRID_ROWS) == (3, 4)
    assert len(ALL_COORDINATES) == 12


def test_coordinates_are_unique_and_row_major():
    assert len(set(ALL_COORDINATES)) == 12
    assert ALL_COORDINATES == (
        "R1C1", "R1C2", "R1C3",
        "R2C1", "R2C2", "R2C3",
        "R3C1", "R3C2", "R3C3",
        "R4C1", "R4C2", "R4C3",
    )  # fmt: skip
    assert [c for _, _, c in all_cells()] == list(ALL_COORDINATES)


def test_coordinate_round_trip():
    for row, column, coordinate in all_cells():
        assert make_coordinate(row, column) == coordinate
        assert parse_coordinate(coordinate) == (row, column)


def test_parse_coordinate_is_case_insensitive():
    assert parse_coordinate("r2c3") == (2, 3)


@pytest.mark.parametrize("bad", ["", "R5C1", "R1C4", "R0C1", "R1C0", "A1", "R1", "1C1", "R10C1", None])
def test_parse_coordinate_rejects_out_of_grid(bad):
    with pytest.raises(ValueError):
        parse_coordinate(bad)


def test_make_coordinate_rejects_out_of_grid():
    with pytest.raises(ValueError):
        make_coordinate(5, 1)


def test_display_status_mapping():
    assert display_status("none", has_blockers=False) == STATUS_EMPTY
    assert display_status("planned", has_blockers=False) == STATUS_PLANNED_AVAILABLE
    assert display_status("planned", has_blockers=True) == STATUS_PLANNED_BLOCKED
    # Blockers never apply to something that already exists in the player's game.
    assert display_status("constructing", has_blockers=True) == STATUS_CONSTRUCTING
    assert display_status("built", has_blockers=True) == STATUS_BUILT


def _leaf(object_id, name="x"):
    return RequirementLeaf(object_id=object_id, name=name)


def test_no_requirements_is_met():
    assert evaluate_requirements([], set()).is_met


def test_and_roots_report_every_unmet_leaf():
    result = evaluate_requirements([_leaf(1, "A"), _leaf(2, "B")], {1})
    assert not result.is_met
    assert [u.description for u in result.unmet] == ["B"]


def test_or_group_met_by_any_alternative():
    group = RequirementGroup("OR", (_leaf(1, "Lasers"), _leaf(2, "Coils")))
    assert evaluate_requirements([group], {2}).is_met


def test_unmet_or_group_is_one_blocker_naming_all_alternatives():
    group = RequirementGroup("OR", (_leaf(1, "Lasers"), _leaf(2, "Coils")))
    result = evaluate_requirements([group], set())
    assert len(result.unmet) == 1
    assert result.unmet[0].description == "(Lasers or Coils)"
    assert {leaf.object_id for leaf in result.unmet[0].leaves} == {1, 2}


def test_nested_groups():
    # (A) AND (B OR (C AND D))
    tree = RequirementGroup(
        "AND",
        (_leaf(1, "A"), RequirementGroup("OR", (_leaf(2, "B"), RequirementGroup("AND", (_leaf(3, "C"), _leaf(4, "D")))))),
    )
    assert evaluate_requirements([tree], {1, 3, 4}).is_met
    assert not evaluate_requirements([tree], {1, 3}).is_met
    assert evaluate_requirements([tree], {1, 2}).is_met
    unmet = evaluate_requirements([tree], {1}).unmet
    assert len(unmet) == 1
