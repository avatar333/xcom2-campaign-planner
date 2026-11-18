"""Avenger Facility Planner service (ADR-0020, FR-PLN-7).

Orchestrates the campaign's own 12-cell grid against the *effective catalog*
(ADR-0013): which facilities and room states exist, what they cost, what they
require, and whether they may be duplicated all come from catalog data -- no
facility or room-state name appears in this module.

Campaign isolation (ADR-0010): every public function takes the campaign and
touches only that campaign's `campaign_avenger_cells` / `campaign_object_state`
rows. Catalog rows are only ever read.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field

from app.domain.avenger_grid import (
    ALL_COORDINATES,
    ASSIGNABLE_BUILD_STATES,
    BUILD_STATE_BUILT,
    BUILD_STATE_CONSTRUCTING,
    BUILD_STATE_LABELS,
    BUILD_STATE_NONE,
    BUILD_STATE_PLANNED,
    GRID_COLUMNS,
    GRID_ROWS,
    STATUS_LABELS,
    display_status,
    parse_coordinate,
)
from app.domain.requirements import (
    RequirementGroup,
    RequirementLeaf,
    RequirementNode,
    evaluate_requirements,
)
from app.extensions import db
from app.models import (
    Campaign,
    CampaignObjectState,
    CatalogObject,
    CatalogPrerequisiteGroup,
    CatalogRelationship,
    CatalogRelationshipType,
)
from app.repositories import avenger_cells as cells_repo
from app.repositories import object_types as object_types_repo
from app.services import asset_service
from app.services import effective_catalog as effective_catalog_service

FACILITY_TYPE_SLUG = "facility"
ROOM_STATE_TYPE_SLUG = "avenger_room_state"
FACILITY_UPGRADE_TYPE_SLUG = "facility_upgrade"

# Inbound relationship kinds that express "the source must be satisfied before
# the target facility can be built" (DOMAIN_MODEL.md §6). `unlocks` is the
# stored inverse of an authored `prerequisite`; `requires_facility` is read in
# the same direction the curated packs author it (source is the requirement).
PREREQUISITE_RELATIONSHIP_SLUGS = ("prerequisite", "unlocks", "requires_research", "requires_facility")
SATISFIED_OBJECT_STATUSES = ("completed", "owned")


class AvengerGridError(ValueError):
    """Raised for invalid grid operations (bad coordinate, bad option, ...)."""


class FacilityAssignmentError(AvengerGridError):
    """Raised when a facility cannot be assigned to a cell."""


# ---------------------------------------------------------------- view data


@dataclass(frozen=True)
class Blocker:
    """One reason a planned facility cannot be built yet. `options` lists the
    catalog objects (id, name) that would resolve it if recorded complete --
    several for an OR group, none for non-recordable blockers."""

    text: str
    options: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class RoomOption:
    object_id: int
    name: str
    short_description: str | None
    gameplay_effect: str | None
    clear_reward: str | None
    primary_asset_id: int | None
    glyph: str
    blocks_construction: bool
    is_default_empty: bool
    is_available: bool = True  # False when the stored state is outside the campaign's sources


@dataclass(frozen=True)
class FacilityOption:
    object_id: int
    name: str
    short_description: str | None
    long_description: str | None
    primary_asset_id: int | None
    glyph: str
    hue: int
    source_name: str | None
    tags: tuple[str, ...]
    power_cost: int | None
    build_cost_supplies: int | None
    build_days: int | None
    staffing: str | None
    allows_duplicates: bool
    ruleset_note: str | None
    blockers: tuple[Blocker, ...]
    placed_at: tuple[str, ...]  # coordinates in this campaign already holding it
    duplicate_blocked: bool
    is_available: bool = True

    @property
    def prerequisites_met(self) -> bool:
        return not self.blockers

    @property
    def power_label(self) -> str | None:
        if self.power_cost is None:
            return None
        if self.power_cost < 0:
            return f"+{abs(self.power_cost)} power generated"
        return f"{self.power_cost} power used"


@dataclass(frozen=True)
class CellView:
    coordinate: str
    row: int
    column: int
    room: RoomOption | None
    facility: FacilityOption | None
    build_state: str
    status: str
    blockers: tuple[Blocker, ...]
    room_blocker: str | None
    notes: str | None

    @property
    def is_configured(self) -> bool:
        return self.room is not None

    @property
    def build_state_label(self) -> str:
        return BUILD_STATE_LABELS[self.build_state]

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def has_warning(self) -> bool:
        return bool(self.blockers) or bool(self.room_blocker)

    @property
    def all_warnings(self) -> list[str]:
        warnings = [blocker.text for blocker in self.blockers]
        if self.room_blocker:
            warnings.append(self.room_blocker)
        return warnings


@dataclass(frozen=True)
class PowerSummary:
    built_draw: int
    built_generated: int
    pending_draw: int  # constructing + planned
    pending_generated: int
    facilities_without_power_data: int


@dataclass(frozen=True)
class GridSummary:
    built: int
    constructing: int
    planned: int
    planned_blocked: int
    occupied_cells: int
    free_cells: int
    configured_cells: int
    unconfigured_cells: int
    hazard_cells: int
    blocked_plans: tuple[tuple[str, str, tuple[str, ...]], ...]  # (coordinate, facility, reasons)
    power: PowerSummary


@dataclass(frozen=True)
class GridView:
    campaign_id: int
    cells: tuple[CellView, ...]
    summary: GridSummary
    room_options: tuple[RoomOption, ...]
    rows: int = GRID_ROWS
    columns: int = GRID_COLUMNS

    @property
    def is_configured(self) -> bool:
        return self.summary.configured_cells > 0

    @property
    def is_fully_configured(self) -> bool:
        return self.summary.unconfigured_cells == 0

    def cell(self, coordinate: str) -> CellView:
        for cell in self.cells:
            if cell.coordinate == coordinate:
                return cell
        raise AvengerGridError(f"Unknown cell '{coordinate}'.")


@dataclass(frozen=True)
class RelatedObject:
    object_id: int
    name: str
    relationship: str


@dataclass(frozen=True)
class FacilityDetail:
    option: FacilityOption
    upgrades: tuple[RelatedObject, ...]
    prerequisite_leaves: tuple[RelatedObject, ...] = field(default_factory=tuple)


# --------------------------------------------------------------- resolution


class _Context:
    """Per-request snapshot of everything the planner needs from the catalog.

    Built once per service call so a page render does a bounded number of
    queries, and so every function agrees on one consistent effective catalog.
    """

    def __init__(self, campaign: Campaign) -> None:
        self.campaign = campaign
        self.effective = effective_catalog_service.get_effective_catalog(
            campaign.id, version=campaign.effective_catalog_version
        )
        self._chain_cache: dict[int, list[int]] = {}
        self.winner_by_member: dict[int, int] = {}
        self.members_by_winner: dict[int, set[int]] = {}
        for winner_id in self.effective:
            members = set(self._chain(winner_id))
            self.members_by_winner[winner_id] = members
            for member_id in members:
                self.winner_by_member[member_id] = winner_id

        self.facility_type = object_types_repo.get_object_type_by_slug(FACILITY_TYPE_SLUG)
        self.room_type = object_types_repo.get_object_type_by_slug(ROOM_STATE_TYPE_SLUG)
        self.type_by_id = {t.id: t for t in object_types_repo.list_object_types()}
        self._source_names: dict[int, str] = {}

    def _chain(self, object_id: int) -> list[int]:
        """`object_id` plus every object it overrides/replaces, transitively."""
        if object_id in self._chain_cache:
            return self._chain_cache[object_id]
        chain: list[int] = []
        seen: set[int] = set()
        current: int | None = object_id
        while current is not None and current not in seen:
            seen.add(current)
            chain.append(current)
            obj = db.session.get(CatalogObject, current)
            current = None if obj is None else (obj.overrides_object_id or obj.replaces_object_id)
        self._chain_cache[object_id] = chain
        return chain

    def winner_of(self, object_id: int) -> int | None:
        return self.winner_by_member.get(object_id)

    def source_name(self, source_id: int | None) -> str | None:
        if source_id is None:
            return None
        if source_id not in self._source_names:
            from app.repositories import content_sources as sources_repo

            source = sources_repo.get_content_source(source_id)
            self._source_names[source_id] = source.name if source else ""
        return self._source_names[source_id] or None

    def effective_of_type(self, type_id: int | None) -> list[dict]:
        if type_id is None:
            return []
        return sorted(
            (e for e in self.effective.values() if e["type_id"] == type_id),
            key=lambda e: (e.get("tier") or 0, e["name"].lower()),
        )


def _hue_for(template_key: str) -> int:
    return zlib.crc32(template_key.encode("utf-8")) % 360


def _as_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_buildable_facility(entry: dict) -> bool:
    return bool((entry.get("attributes") or {}).get("buildable_in_avenger_grid", True))


def _room_option_from_entry(ctx: _Context, entry: dict, *, is_available: bool = True) -> RoomOption:
    attrs = entry.get("attributes") or {}
    blocks = attrs.get("blocks_construction")
    if blocks is None:
        blocks = attrs.get("clearable", False)
    return RoomOption(
        object_id=entry["id"],
        name=entry["name"],
        short_description=entry.get("short_description"),
        gameplay_effect=attrs.get("gameplay_effect"),
        clear_reward=attrs.get("clear_reward"),
        primary_asset_id=entry.get("primary_asset_id"),
        glyph=asset_service.fallback_glyph_for_type(ctx.type_by_id.get(entry["type_id"])),
        blocks_construction=bool(blocks),
        is_default_empty=bool(attrs.get("is_default_empty", False)),
        is_available=is_available,
    )


def _room_option_for_id(ctx: _Context, object_id: int | None) -> RoomOption | None:
    if object_id is None:
        return None
    winner = ctx.winner_of(object_id)
    if winner is not None and winner in ctx.effective:
        return _room_option_from_entry(ctx, ctx.effective[winner])
    obj = db.session.get(CatalogObject, object_id)
    if obj is None:
        return None
    from app.domain.attributes import parse_attributes_json

    fallback = {
        "id": obj.id,
        "type_id": obj.type_id,
        "name": obj.name,
        "short_description": obj.short_description,
        "primary_asset_id": obj.primary_asset_id,
        "attributes": parse_attributes_json(obj.attributes),
    }
    return _room_option_from_entry(ctx, fallback, is_available=False)


# ------------------------------------------------------------ prerequisites


def _satisfied_ids(ctx: _Context, cells: list) -> set[int]:
    """Normalized (winner) ids of everything currently satisfied for the
    campaign: recorded-complete objects plus facilities already built."""
    satisfied: set[int] = set()
    rows = CampaignObjectState.query.filter_by(campaign_id=ctx.campaign.id).all()
    for row in rows:
        if row.status in SATISFIED_OBJECT_STATUSES or row.prerequisite_override:
            satisfied.add(ctx.winner_of(row.catalog_object_id) or row.catalog_object_id)
    for cell in cells:
        if cell.facility_object_id is not None and cell.build_state == BUILD_STATE_BUILT:
            satisfied.add(ctx.winner_of(cell.facility_object_id) or cell.facility_object_id)
    return satisfied


def _leaf_for(ctx: _Context, object_id: int, kind: str) -> RequirementLeaf:
    winner = ctx.winner_of(object_id) or object_id
    entry = ctx.effective.get(winner)
    if entry is not None:
        name = entry["name"]
    else:
        obj = db.session.get(CatalogObject, object_id)
        name = obj.name if obj is not None else f"Object {object_id}"
    return RequirementLeaf(object_id=winner, name=name, kind=kind)


def _group_tree(ctx: _Context, group: CatalogPrerequisiteGroup) -> RequirementNode:
    from app.repositories import prerequisite_groups as groups_repo

    members: list[RequirementNode] = []
    for member in groups_repo.list_members(group.id):
        if member.child_group_id is not None:
            child = groups_repo.get_group(member.child_group_id)
            if child is not None:
                members.append(_group_tree(ctx, child))
        elif member.catalog_object_id is not None:
            members.append(_leaf_for(ctx, member.catalog_object_id, "prerequisite"))
    return RequirementGroup(logic=group.logic, members=tuple(members))


def _requirement_roots(ctx: _Context, facility_id: int) -> list[RequirementNode]:
    """Factual prerequisites of a facility, read from the catalog: flat
    relationship rows (grouped by `group_key`, ADR-0004) plus compound
    AND/OR trees (ADR-0018). Edges on any object the facility overrides are
    inherited, because an override patches rather than copies (ADR-0013)."""
    member_ids = set(ctx.members_by_winner.get(facility_id, {facility_id}))
    type_ids = [
        t.id
        for t in CatalogRelationshipType.query.filter(
            CatalogRelationshipType.slug.in_(PREREQUISITE_RELATIONSHIP_SLUGS)
        ).all()
    ]
    edges = (
        CatalogRelationship.query.filter(
            CatalogRelationship.target_object_id.in_(member_ids),
            CatalogRelationship.relationship_type_id.in_(type_ids),
        )
        .order_by(CatalogRelationship.sort_order.asc(), CatalogRelationship.id.asc())
        .all()
        if type_ids
        else []
    )
    slug_by_type_id = {t.id: t.slug for t in CatalogRelationshipType.query.all()}

    roots: list[RequirementNode] = []
    seen_leaves: set[tuple[int, str | None]] = set()
    grouped: dict[str, list[CatalogRelationship]] = {}
    ordered_group_keys: list[str] = []
    for edge in edges:
        if edge.group_key:
            if edge.group_key not in grouped:
                ordered_group_keys.append(edge.group_key)
            grouped.setdefault(edge.group_key, []).append(edge)
            continue
        leaf = _leaf_for(ctx, edge.source_object_id, slug_by_type_id[edge.relationship_type_id])
        if (leaf.object_id, None) in seen_leaves:
            continue
        seen_leaves.add((leaf.object_id, None))
        roots.append(leaf)
    for key in ordered_group_keys:
        group_edges = grouped[key]
        logic = next((e.group_logic for e in group_edges if e.group_logic), "AND")
        leaves = tuple(
            _leaf_for(ctx, e.source_object_id, slug_by_type_id[e.relationship_type_id])
            for e in group_edges
        )
        roots.append(RequirementGroup(logic=logic, members=leaves))

    compound = CatalogPrerequisiteGroup.query.filter(
        CatalogPrerequisiteGroup.target_object_id.in_(member_ids),
        CatalogPrerequisiteGroup.parent_group_id.is_(None),
    ).all()
    for group in compound:
        roots.append(_group_tree(ctx, group))
    return roots


def _blockers_for(ctx: _Context, facility_id: int, satisfied: set[int]) -> tuple[Blocker, ...]:
    evaluation = evaluate_requirements(_requirement_roots(ctx, facility_id), satisfied)
    blockers: list[Blocker] = []
    for unmet in evaluation.unmet:
        recordable = tuple(
            (leaf.object_id, leaf.name)
            for leaf in unmet.leaves
            if not _is_facility_id(ctx, leaf.object_id)
        )
        if len(unmet.leaves) == 1:
            leaf = unmet.leaves[0]
            noun = "facility" if _is_facility_id(ctx, leaf.object_id) else "prerequisite"
            blockers.append(Blocker(f"{leaf.name} {noun} not recorded as complete", recordable))
        else:
            blockers.append(Blocker(f"None of {unmet.description} recorded as complete", recordable))
    return tuple(blockers)


def _is_facility_id(ctx: _Context, object_id: int) -> bool:
    entry = ctx.effective.get(object_id)
    return bool(entry and ctx.facility_type is not None and entry["type_id"] == ctx.facility_type.id)


# ------------------------------------------------------------ option building


def _facility_option(
    ctx: _Context,
    entry: dict,
    *,
    satisfied: set[int],
    placed_elsewhere: dict[int, list[str]],
    is_available: bool = True,
) -> FacilityOption:
    attrs = entry.get("attributes") or {}
    allows_duplicates = bool(attrs.get("allows_duplicates", False))
    placed = tuple(placed_elsewhere.get(entry["id"], ()))
    return FacilityOption(
        object_id=entry["id"],
        name=entry["name"],
        short_description=entry.get("short_description"),
        long_description=entry.get("long_description"),
        primary_asset_id=entry.get("primary_asset_id"),
        glyph=asset_service.fallback_glyph_for_type(ctx.facility_type),
        hue=_hue_for(entry.get("template_key") or entry["name"]),
        source_name=ctx.source_name(entry.get("source_id")),
        tags=tuple(_tags_for(entry["id"])),
        power_cost=_as_int(attrs.get("power_cost")),
        build_cost_supplies=_as_int(attrs.get("build_cost_supplies")),
        build_days=_as_int(attrs.get("build_days")),
        staffing=attrs.get("staffing_slots"),
        allows_duplicates=allows_duplicates,
        ruleset_note=attrs.get("ruleset_note"),
        blockers=_blockers_for(ctx, entry["id"], satisfied),
        placed_at=placed,
        duplicate_blocked=bool(placed) and not allows_duplicates,
        is_available=is_available,
    )


def _tags_for(object_id: int) -> list[str]:
    from app.models import CatalogObjectTag

    return [t.tag for t in CatalogObjectTag.query.filter_by(object_id=object_id).all()]


def _placed_map(ctx: _Context, cells: list, *, exclude_coordinate: str | None = None) -> dict[int, list[str]]:
    placed: dict[int, list[str]] = {}
    for cell in cells:
        if cell.facility_object_id is None or cell.coordinate == exclude_coordinate:
            continue
        winner = ctx.winner_of(cell.facility_object_id) or cell.facility_object_id
        placed.setdefault(winner, []).append(cell.coordinate)
    return placed


def _facility_option_for_stored(
    ctx: _Context, object_id: int | None, *, satisfied: set[int], placed: dict[int, list[str]]
) -> FacilityOption | None:
    if object_id is None:
        return None
    winner = ctx.winner_of(object_id)
    if winner is not None and winner in ctx.effective and _is_buildable_facility(ctx.effective[winner]):
        return _facility_option(ctx, ctx.effective[winner], satisfied=satisfied, placed_elsewhere=placed)
    obj = db.session.get(CatalogObject, object_id)
    if obj is None:
        return None
    from app.domain.attributes import parse_attributes_json

    fallback = {
        "id": obj.id,
        "type_id": obj.type_id,
        "template_key": obj.template_key,
        "source_id": obj.source_id,
        "name": obj.name,
        "short_description": obj.short_description,
        "long_description": obj.long_description,
        "primary_asset_id": obj.primary_asset_id,
        "attributes": parse_attributes_json(obj.attributes),
    }
    return _facility_option(
        ctx, fallback, satisfied=satisfied, placed_elsewhere=placed, is_available=False
    )


# ------------------------------------------------------------------ public


def ensure_grid(campaign: Campaign) -> None:
    """Create the campaign's 12 cells if they do not exist (idempotent)."""
    if cells_repo.ensure_cells(campaign.id):
        db.session.commit()


def list_room_options(campaign: Campaign) -> list[RoomOption]:
    ctx = _Context(campaign)
    return [_room_option_from_entry(ctx, e) for e in ctx.effective_of_type(ctx.room_type.id if ctx.room_type else None)]


def list_facility_options(
    campaign: Campaign, *, for_coordinate: str | None = None
) -> list[FacilityOption]:
    """Every facility buildable in the Avenger grid for this campaign's active
    sources/ruleset, with prerequisite blockers and duplicate status. A facility
    that is already placed elsewhere and does not allow duplicates is still
    listed (flagged `duplicate_blocked`) so the UI can explain why."""
    ensure_grid(campaign)
    ctx = _Context(campaign)
    cells = cells_repo.list_cells(campaign.id)
    satisfied = _satisfied_ids(ctx, cells)
    placed = _placed_map(ctx, cells, exclude_coordinate=for_coordinate)
    entries = [
        e
        for e in ctx.effective_of_type(ctx.facility_type.id if ctx.facility_type else None)
        if _is_buildable_facility(e)
    ]
    return [_facility_option(ctx, e, satisfied=satisfied, placed_elsewhere=placed) for e in entries]


def get_grid_view(campaign: Campaign) -> GridView:
    ensure_grid(campaign)
    ctx = _Context(campaign)
    cells = cells_repo.list_cells(campaign.id)
    satisfied = _satisfied_ids(ctx, cells)

    views: list[CellView] = []
    for cell in cells:
        room = _room_option_for_id(ctx, cell.room_state_object_id)
        placed_others = _placed_map(ctx, cells, exclude_coordinate=cell.coordinate)
        facility = _facility_option_for_stored(
            ctx, cell.facility_object_id, satisfied=satisfied, placed=placed_others
        )
        blockers = facility.blockers if facility and cell.build_state == BUILD_STATE_PLANNED else ()
        room_blocker = None
        if (
            facility is not None
            and cell.build_state == BUILD_STATE_PLANNED
            and room is not None
            and room.blocks_construction
        ):
            room_blocker = f"{room.name} must be cleared before construction"
        if facility is not None and not facility.is_available:
            blockers = (*blockers, Blocker("Not available in this campaign's current content sources"))
        views.append(
            CellView(
                coordinate=cell.coordinate,
                row=cell.grid_row,
                column=cell.grid_column,
                room=room,
                facility=facility,
                build_state=cell.build_state,
                status=display_status(cell.build_state, has_blockers=bool(blockers or room_blocker)),
                blockers=blockers,
                room_blocker=room_blocker,
                notes=cell.notes,
            )
        )

    return GridView(
        campaign_id=campaign.id,
        cells=tuple(views),
        summary=_summarize(views),
        room_options=tuple(
            _room_option_from_entry(ctx, e)
            for e in ctx.effective_of_type(ctx.room_type.id if ctx.room_type else None)
        ),
    )


def _summarize(cells: list[CellView]) -> GridSummary:
    built = constructing = planned = planned_blocked = hazards = 0
    blocked_plans: list[tuple[str, str, tuple[str, ...]]] = []
    built_draw = built_gen = pending_draw = pending_gen = missing_power = 0
    for cell in cells:
        if cell.facility is None:
            if cell.room is not None and cell.room.blocks_construction:
                hazards += 1
            continue
        if cell.build_state == BUILD_STATE_BUILT:
            built += 1
        elif cell.build_state == BUILD_STATE_CONSTRUCTING:
            constructing += 1
        elif cell.build_state == BUILD_STATE_PLANNED:
            planned += 1
            if cell.has_warning:
                planned_blocked += 1
                blocked_plans.append((cell.coordinate, cell.facility.name, tuple(cell.all_warnings)))
        power = cell.facility.power_cost
        if power is None:
            missing_power += 1
            continue
        draw, gen = (power, 0) if power >= 0 else (0, -power)
        if cell.build_state == BUILD_STATE_BUILT:
            built_draw += draw
            built_gen += gen
        else:
            pending_draw += draw
            pending_gen += gen
    configured = sum(1 for c in cells if c.is_configured)
    occupied = sum(1 for c in cells if c.facility is not None)
    return GridSummary(
        built=built,
        constructing=constructing,
        planned=planned,
        planned_blocked=planned_blocked,
        occupied_cells=occupied,
        free_cells=len(cells) - occupied,
        configured_cells=configured,
        unconfigured_cells=len(cells) - configured,
        hazard_cells=hazards,
        blocked_plans=tuple(blocked_plans),
        power=PowerSummary(
            built_draw=built_draw,
            built_generated=built_gen,
            pending_draw=pending_draw,
            pending_generated=pending_gen,
            facilities_without_power_data=missing_power,
        ),
    )


def get_cell_view(campaign: Campaign, coordinate: str) -> CellView:
    return get_grid_view(campaign).cell(_validated_coordinate(coordinate))


def get_facility_detail(
    campaign: Campaign, object_id: int, *, for_coordinate: str | None = None
) -> FacilityDetail:
    option = next(
        (o for o in list_facility_options(campaign, for_coordinate=for_coordinate) if o.object_id == _normalize_id(campaign, object_id)),
        None,
    )
    if option is None:
        raise AvengerGridError("That facility is not buildable in this campaign.")
    ctx = _Context(campaign)
    related: list[RelatedObject] = []
    member_ids = ctx.members_by_winner.get(option.object_id, {option.object_id})
    seen: set[int] = set()
    upgrade_type = object_types_repo.get_object_type_by_slug(FACILITY_UPGRADE_TYPE_SLUG)
    type_by_id = {t.id: t.slug for t in CatalogRelationshipType.query.all()}
    edges = CatalogRelationship.query.filter(
        (CatalogRelationship.source_object_id.in_(member_ids))
        | (CatalogRelationship.target_object_id.in_(member_ids))
    ).all()
    for edge in edges:
        other_id = edge.target_object_id if edge.source_object_id in member_ids else edge.source_object_id
        other = db.session.get(CatalogObject, other_id)
        if other is None or other.id in seen or other.is_retired:
            continue
        is_upgrade_object = upgrade_type is not None and other.type_id == upgrade_type.id
        if is_upgrade_object or type_by_id.get(edge.relationship_type_id) in ("upgrades_to", "upgraded_from"):
            seen.add(other.id)
            related.append(RelatedObject(other.id, other.name, type_by_id.get(edge.relationship_type_id, "")))
    leaves = []
    for root in _requirement_roots(ctx, option.object_id):
        stack = [root]
        while stack:
            node = stack.pop(0)
            if isinstance(node, RequirementLeaf):
                leaves.append(RelatedObject(node.object_id, node.name, node.kind))
            else:
                stack.extend(node.members)
    return FacilityDetail(option=option, upgrades=tuple(related), prerequisite_leaves=tuple(leaves))


# -------------------------------------------------------------- mutations


def _validated_coordinate(coordinate: str) -> str:
    try:
        parse_coordinate(coordinate)
    except ValueError as exc:
        raise AvengerGridError(str(exc)) from exc
    return coordinate.strip().upper()


def _normalize_id(campaign: Campaign, object_id: int) -> int | None:
    return _Context(campaign).winner_of(object_id)


def _cell_for_update(campaign: Campaign, coordinate: str):
    coordinate = _validated_coordinate(coordinate)
    ensure_grid(campaign)
    cell = cells_repo.get_cell(campaign.id, coordinate)
    if cell is None:  # pragma: no cover - ensure_grid guarantees all 12
        raise AvengerGridError(f"Cell {coordinate} does not exist.")
    return cell


def _validated_room_id(ctx: _Context, room_object_id: int) -> int:
    winner = ctx.winner_of(room_object_id)
    entry = ctx.effective.get(winner) if winner is not None else None
    if entry is None or ctx.room_type is None or entry["type_id"] != ctx.room_type.id:
        raise AvengerGridError("That is not a room state available to this campaign.")
    return winner


def set_room_state(campaign: Campaign, coordinate: str, room_object_id: int | None) -> None:
    cell = _cell_for_update(campaign, coordinate)
    if room_object_id is None:
        cell.room_state_object_id = None
    else:
        cell.room_state_object_id = _validated_room_id(_Context(campaign), room_object_id)
    db.session.commit()


def configure_layout(campaign: Campaign, room_by_coordinate: dict[str, int]) -> int:
    """Apply a whole starting-layout draft atomically: either every entry is
    valid and saved, or nothing changes. Cells not mentioned are left as they
    are (so a partially filled draft never silently resets other cells).
    Returns the number of cells written."""
    ensure_grid(campaign)
    ctx = _Context(campaign)
    resolved: dict[str, int] = {}
    for raw_coordinate, room_id in room_by_coordinate.items():
        resolved[_validated_coordinate(raw_coordinate)] = _validated_room_id(ctx, room_id)
    cells = {c.coordinate: c for c in cells_repo.list_cells(campaign.id)}
    try:
        for coordinate, room_id in resolved.items():
            cells[coordinate].room_state_object_id = room_id
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return len(resolved)


def reset_layout(campaign: Campaign, *, clear_facilities: bool = False) -> None:
    """Reset every cell's room state to the catalog's default-empty room state
    (the one flagged `is_default_empty`). If the ruleset defines none, cells
    return to 'not configured'. Facility plans are kept unless asked."""
    ensure_grid(campaign)
    ctx = _Context(campaign)
    default_id = next(
        (
            e["id"]
            for e in ctx.effective_of_type(ctx.room_type.id if ctx.room_type else None)
            if (e.get("attributes") or {}).get("is_default_empty")
        ),
        None,
    )
    for cell in cells_repo.list_cells(campaign.id):
        cell.room_state_object_id = default_id
        if clear_facilities:
            cell.facility_object_id = None
            cell.build_state = BUILD_STATE_NONE
    db.session.commit()


def assign_facility(
    campaign: Campaign,
    coordinate: str,
    facility_object_id: int,
    *,
    build_state: str = BUILD_STATE_PLANNED,
) -> None:
    """Assign a facility to a cell with a lifecycle state. Prerequisites never
    prevent *planning* (they are reported by the views); validity does: the
    facility must be a buildable facility in this campaign's effective catalog,
    and a non-duplicable facility may occupy only one cell."""
    if build_state not in ASSIGNABLE_BUILD_STATES:
        raise FacilityAssignmentError(f"'{build_state}' is not a valid build state.")
    cell = _cell_for_update(campaign, coordinate)
    ctx = _Context(campaign)
    winner = ctx.winner_of(facility_object_id)
    entry = ctx.effective.get(winner) if winner is not None else None
    if (
        entry is None
        or ctx.facility_type is None
        or entry["type_id"] != ctx.facility_type.id
        or not _is_buildable_facility(entry)
    ):
        raise FacilityAssignmentError(
            "That is not a buildable Avenger facility for this campaign's content sources."
        )
    allows_duplicates = bool((entry.get("attributes") or {}).get("allows_duplicates", False))
    if not allows_duplicates:
        others = _placed_map(ctx, cells_repo.list_cells(campaign.id), exclude_coordinate=cell.coordinate)
        if winner in others:
            raise FacilityAssignmentError(
                f"{entry['name']} can only be built once and is already placed at "
                f"{', '.join(others[winner])}."
            )
    cell.facility_object_id = winner
    cell.build_state = build_state
    db.session.commit()


def set_build_state(campaign: Campaign, coordinate: str, build_state: str) -> None:
    if build_state not in ASSIGNABLE_BUILD_STATES:
        raise FacilityAssignmentError(f"'{build_state}' is not a valid build state.")
    cell = _cell_for_update(campaign, coordinate)
    if cell.facility_object_id is None:
        raise FacilityAssignmentError("Choose a facility for this cell before setting its build state.")
    cell.build_state = build_state
    db.session.commit()


def clear_facility(campaign: Campaign, coordinate: str) -> None:
    cell = _cell_for_update(campaign, coordinate)
    cell.facility_object_id = None
    cell.build_state = BUILD_STATE_NONE
    db.session.commit()


def set_prerequisite_state(campaign: Campaign, object_id: int, *, completed: bool) -> None:
    """Record (or un-record) that a prerequisite object is complete for *this*
    campaign, in `campaign_object_state` keyed by the natural key
    `(campaign_id, catalog_object_id)` (ADR-0010). The object must be visible in
    the campaign's effective catalog."""
    ctx = _Context(campaign)
    winner = ctx.winner_of(object_id)
    if winner is None:
        raise AvengerGridError("That object is not part of this campaign's catalog.")
    row = CampaignObjectState.query.filter_by(campaign_id=campaign.id, catalog_object_id=winner).first()
    if completed:
        if row is None:
            row = CampaignObjectState(campaign_id=campaign.id, catalog_object_id=winner, status="completed")
            db.session.add(row)
        else:
            row.status = "completed"
    elif row is not None:
        row.status = "unknown"
    db.session.commit()


def ensure_grid_for_new_campaign(campaign: Campaign) -> None:
    """Hook for campaign creation: give every new campaign its own 12 cells."""
    ensure_grid(campaign)


__all__ = [
    "ALL_COORDINATES",
    "BUILD_STATE_CONSTRUCTING",
    "AvengerGridError",
    "FacilityAssignmentError",
    "assign_facility",
    "clear_facility",
    "configure_layout",
    "ensure_grid",
    "get_cell_view",
    "get_facility_detail",
    "get_grid_view",
    "list_facility_options",
    "list_room_options",
    "reset_layout",
    "set_build_state",
    "set_prerequisite_state",
    "set_room_state",
]
