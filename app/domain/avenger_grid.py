"""Avenger construction-grid geometry and build-state vocabulary (ADR-0020).

Pure module (no Flask/SQLAlchemy). The grid is 3 columns across by 4 rows
down. Positions are *stable coordinates* ("R<row>C<col>", 1-based) that are
stored as data -- never inferred from display ordering.
"""
from __future__ import annotations

import re

GRID_ROWS = 4
GRID_COLUMNS = 3

COORDINATE_PATTERN = re.compile(r"^R([1-9])C([1-9])$")


def make_coordinate(row: int, column: int) -> str:
    if not (1 <= row <= GRID_ROWS and 1 <= column <= GRID_COLUMNS):
        raise ValueError(f"Cell ({row}, {column}) is outside the {GRID_ROWS}x{GRID_COLUMNS} grid.")
    return f"R{row}C{column}"


def parse_coordinate(coordinate: str) -> tuple[int, int]:
    """Return `(row, column)` for a valid coordinate, else raise ValueError."""
    match = COORDINATE_PATTERN.match((coordinate or "").strip().upper())
    if match is None:
        raise ValueError(f"'{coordinate}' is not a valid Avenger grid coordinate.")
    row, column = int(match.group(1)), int(match.group(2))
    make_coordinate(row, column)
    return row, column


def all_cells() -> list[tuple[int, int, str]]:
    """Row-major `(row, column, coordinate)` for every one of the 12 cells."""
    return [
        (row, column, make_coordinate(row, column))
        for row in range(1, GRID_ROWS + 1)
        for column in range(1, GRID_COLUMNS + 1)
    ]


ALL_COORDINATES: tuple[str, ...] = tuple(coordinate for _, _, coordinate in all_cells())

# Facility lifecycle stored on a cell. "none" means no facility is assigned.
# "Available to build" and "Blocked" are NOT stored: they are derived from the
# catalog prerequisites for a *planned* facility (see `display_status`).
BUILD_STATE_NONE = "none"
BUILD_STATE_PLANNED = "planned"
BUILD_STATE_CONSTRUCTING = "constructing"
BUILD_STATE_BUILT = "built"
BUILD_STATES = (
    BUILD_STATE_NONE,
    BUILD_STATE_PLANNED,
    BUILD_STATE_CONSTRUCTING,
    BUILD_STATE_BUILT,
)
ASSIGNABLE_BUILD_STATES = (BUILD_STATE_PLANNED, BUILD_STATE_CONSTRUCTING, BUILD_STATE_BUILT)

BUILD_STATE_LABELS = {
    BUILD_STATE_NONE: "None",
    BUILD_STATE_PLANNED: "Planned",
    BUILD_STATE_CONSTRUCTING: "Constructing",
    BUILD_STATE_BUILT: "Built",
}

# Derived display statuses (what the UI badges show).
STATUS_EMPTY = "empty"
STATUS_PLANNED_BLOCKED = "planned_blocked"
STATUS_PLANNED_AVAILABLE = "planned_available"
STATUS_CONSTRUCTING = "constructing"
STATUS_BUILT = "built"

STATUS_LABELS = {
    STATUS_EMPTY: "No facility",
    STATUS_PLANNED_BLOCKED: "Planned - Blocked",
    STATUS_PLANNED_AVAILABLE: "Available to build",
    STATUS_CONSTRUCTING: "Constructing",
    STATUS_BUILT: "Built",
}


def display_status(build_state: str, *, has_blockers: bool) -> str:
    """Map a stored build state (+ whether prerequisites block it) to the
    status shown to the user. Blockers only matter while a facility is merely
    planned: a facility that is under construction or built demonstrably
    exists in the player's game, so it is never shown as blocked."""
    if build_state == BUILD_STATE_PLANNED:
        return STATUS_PLANNED_BLOCKED if has_blockers else STATUS_PLANNED_AVAILABLE
    if build_state == BUILD_STATE_CONSTRUCTING:
        return STATUS_CONSTRUCTING
    if build_state == BUILD_STATE_BUILT:
        return STATUS_BUILT
    return STATUS_EMPTY
