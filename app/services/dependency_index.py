"""Catalog-side dependency index (ADR-0021, ADR-0018, ADR-0013).

Turns the *effective catalog* of one campaign plus its recorded progress into the
plain inputs of the pure domain layer (`app.domain.dependency_graph`):

- per-object requirement expressions, built from catalog relationships
  (`group_key` AND/OR groups, ADR-0004) and compound nested groups (ADR-0018);
- per-object facts (name, type, recorded status: completed / in progress /
  planned);
- decorated object references used by every screen so status is rendered
  identically everywhere.

Nothing here knows what any specific game object is. The relationship slugs it
reads are the generic seeded vocabulary (DOMAIN_MODEL.md §6); the directions
follow the packs' authoring convention: the *source* of every requirement-style
relationship is the thing that must be satisfied first, the *target* is the
thing it enables (`requires_research A -> B` means B requires A).

Ruleset awareness
- Only relationships/groups authored by a source **enabled for the campaign**
  count, so a mod that adds a prerequisite to a base object affects only
  campaigns that enabled the mod.
- An override object (`overrides_object_id`) inherits the requirements of the
  object it overrides *unless it declares requirements of its own*, in which
  case its own replace them (the LWotC Plasma Rifle changes the requirement
  rather than adding to it). `replaces_object_id` never inherits.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.dependency_graph import (
    RECORDED_COMPLETED,
    RECORDED_IN_PROGRESS,
    RECORDED_NONE,
    RECORDED_PLANNED,
    STATUS_AVAILABLE,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_PLANNED,
    ObjectFacts,
    ObjectStatus,
    compute_object_statuses,
)
from app.domain.requirements import (
    RequirementGroup,
    RequirementLeaf,
    RequirementNode,
    UnmetRequirement,
)
from app.extensions import db
from app.models import (
    Campaign,
    CampaignAvengerCell,
    CampaignObjectState,
    CampaignResearchQueueItem,
    CatalogObject,
    CatalogObjectTag,
    CatalogPrerequisiteGroup,
    CatalogRelationship,
    CatalogRelationshipType,
)
from app.repositories import campaign_sources as campaign_sources_repo
from app.repositories import content_sources as sources_repo
from app.repositories import object_types as object_types_repo
from app.repositories import prerequisite_groups as groups_repo
from app.services import asset_service
from app.services import effective_catalog as effective_catalog_service

# Seeded relationship vocabulary that expresses "source must be satisfied
# before target". `requires_resource` is shown but never gates (ADR-0021).
REQUIREMENT_RELATIONSHIP_SLUGS = (
    "prerequisite",
    "unlocks",
    "requires_research",
    "requires_facility",
    "requires_resource",
    "produced_by",
)
NON_GATING_RELATIONSHIP_SLUGS = ("requires_resource",)

# Object types the player acts on (and therefore *completes*), as opposed to
# things that are merely unlocked. Structural type slugs, not game content.
PROJECT_TYPE_SLUGS = ("research_project", "autopsy_project", "proving_ground_project", "technology")
ACTION_TYPE_SLUGS = (*PROJECT_TYPE_SLUGS, "facility", "mission", "covert_action", "strategy_upgrade")
NON_GATING_TYPE_SLUGS = ("resource",)

CATEGORY_PROJECT = "project"
CATEGORY_FACILITY = "facility"
CATEGORY_RESOURCE = "resource"
CATEGORY_ACTION = "action"
CATEGORY_UNLOCKABLE = "unlockable"

SATISFIED_STATE_STATUSES = ("completed", "owned")


def category_of(type_slug: str) -> str:
    if type_slug in PROJECT_TYPE_SLUGS:
        return CATEGORY_PROJECT
    if type_slug == "facility":
        return CATEGORY_FACILITY
    if type_slug in NON_GATING_TYPE_SLUGS:
        return CATEGORY_RESOURCE
    if type_slug in ACTION_TYPE_SLUGS:
        return CATEGORY_ACTION
    return CATEGORY_UNLOCKABLE


_STATUS_LABELS = {
    CATEGORY_PROJECT: {
        STATUS_COMPLETED: "Completed",
        STATUS_IN_PROGRESS: "In progress",
        STATUS_PLANNED: "Planned",
        STATUS_AVAILABLE: "Available",
        STATUS_BLOCKED: "Locked",
    },
    CATEGORY_FACILITY: {
        STATUS_COMPLETED: "Built",
        STATUS_IN_PROGRESS: "Constructing",
        STATUS_PLANNED: "Planned",
        STATUS_AVAILABLE: "Available to build",
        STATUS_BLOCKED: "Locked",
    },
    CATEGORY_UNLOCKABLE: {
        STATUS_COMPLETED: "Owned",
        STATUS_IN_PROGRESS: "In progress",
        STATUS_PLANNED: "Planned",
        STATUS_AVAILABLE: "Unlocked",
        STATUS_BLOCKED: "Locked",
    },
}
_STATUS_LABELS[CATEGORY_ACTION] = _STATUS_LABELS[CATEGORY_PROJECT]


def status_label(type_slug: str, status: str) -> str:
    category = category_of(type_slug)
    if category == CATEGORY_RESOURCE:
        return "Not tracked"
    return _STATUS_LABELS[category].get(status, status.replace("_", " ").title())


@dataclass(frozen=True)
class Blocker:
    """One reason something cannot proceed. `options` lists the catalog objects
    (id, name) that would resolve it if recorded complete: several for an OR
    group, none when it can only be resolved by building something."""

    text: str
    options: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class ObjectRef:
    """A catalog object decorated with this campaign's status."""

    object_id: int
    name: str
    type_slug: str
    type_name: str
    category: str
    status: str
    status_label: str
    satisfied: bool
    requirements_met: bool
    primary_asset_id: int | None
    glyph: str
    source_name: str | None = None
    is_alternative: bool = False
    amount: float | None = None
    other_required: tuple[str, ...] = ()
    relationship: str | None = None

    @property
    def is_blocked(self) -> bool:
        return not self.requirements_met and not self.satisfied


class DependencyIndex:
    """Per-request snapshot of the campaign's effective catalog, requirement
    expressions and recorded progress. Built once and reused so a page render
    does a bounded number of queries and every part of it agrees."""

    def __init__(self, campaign: Campaign) -> None:
        self.campaign = campaign
        self.effective = effective_catalog_service.get_effective_catalog(
            campaign.id, version=campaign.effective_catalog_version
        )
        self.enabled_source_ids: set[int] = set(campaign_sources_repo.enabled_source_ids(campaign.id))
        self.type_by_id = {t.id: t for t in object_types_repo.list_object_types()}
        self._source_names: dict[int, str] = {}

        parents = db.session.query(
            CatalogObject.id, CatalogObject.overrides_object_id, CatalogObject.replaces_object_id
        ).all()
        self._overrides = {row[0]: row[1] for row in parents if row[1]}
        self._replaces = {row[0]: row[2] for row in parents if row[2]}

        self.winner_by_member: dict[int, int] = {}
        self.members_by_winner: dict[int, set[int]] = {}
        for winner_id in self.effective:
            members = set(self._chain(winner_id, follow_replaces=True))
            self.members_by_winner[winner_id] = members
            for member_id in members:
                self.winner_by_member[member_id] = winner_id

        self._roots: dict[int, list[RequirementNode]] | None = None
        self._facts: dict[int, ObjectFacts] | None = None
        self._statuses: dict[int, ObjectStatus] | None = None
        self._queue_rows: list[CampaignResearchQueueItem] | None = None
        self._state_rows: list[CampaignObjectState] | None = None
        self._refs: dict[int, ObjectRef] = {}

    # ---------------------------------------------------------- identity

    def _chain(self, object_id: int, *, follow_replaces: bool) -> list[int]:
        chain: list[int] = []
        seen: set[int] = set()
        current: int | None = object_id
        while current is not None and current not in seen:
            seen.add(current)
            chain.append(current)
            parent = self._overrides.get(current)
            if parent is None and follow_replaces:
                parent = self._replaces.get(current)
            current = parent
        return chain

    def winner_of(self, object_id: int) -> int | None:
        return self.winner_by_member.get(object_id)

    def normalize(self, object_id: int) -> int:
        return self.winner_by_member.get(object_id, object_id)

    def type_slug(self, object_id: int) -> str:
        entry = self.effective.get(object_id)
        if entry is not None:
            return self.type_by_id[entry["type_id"]].slug
        obj = db.session.get(CatalogObject, object_id)
        return self.type_by_id[obj.type_id].slug if obj is not None else ""

    def source_name(self, source_id: int | None) -> str | None:
        if source_id is None:
            return None
        if source_id not in self._source_names:
            source = sources_repo.get_content_source(source_id)
            self._source_names[source_id] = source.name if source else ""
        return self._source_names[source_id] or None

    def effective_of_type_slugs(self, slugs: tuple[str, ...]) -> list[dict]:
        wanted = {t.id for t in self.type_by_id.values() if t.slug in slugs}
        return sorted(
            (e for e in self.effective.values() if e["type_id"] in wanted),
            key=lambda e: (e["name"].lower(), e["id"]),
        )

    def effective_of_type(self, type_id: int | None) -> list[dict]:
        if type_id is None:
            return []
        return sorted(
            (e for e in self.effective.values() if e["type_id"] == type_id),
            key=lambda e: (e.get("tier") or 0, e["name"].lower()),
        )

    def tags_for(self, object_id: int) -> tuple[str, ...]:
        return tuple(t.tag for t in CatalogObjectTag.query.filter_by(object_id=object_id).all())

    # -------------------------------------------------------- requirements

    def requirement_map(self) -> dict[int, list[RequirementNode]]:
        if self._roots is None:
            self._roots = self._build_requirement_map()
        return self._roots

    def roots_for(self, object_id: int) -> list[RequirementNode]:
        return self.requirement_map().get(self.normalize(object_id), [])

    def _build_requirement_map(self) -> dict[int, list[RequirementNode]]:
        slug_by_type = {t.id: t.slug for t in CatalogRelationshipType.query.all()}
        wanted_type_ids = [i for i, slug in slug_by_type.items() if slug in REQUIREMENT_RELATIONSHIP_SLUGS]
        edges: list[CatalogRelationship] = []
        if wanted_type_ids and self.enabled_source_ids:
            edges = (
                CatalogRelationship.query.filter(
                    CatalogRelationship.relationship_type_id.in_(wanted_type_ids),
                    CatalogRelationship.source_pack_id.in_(self.enabled_source_ids),
                )
                .order_by(CatalogRelationship.sort_order.asc(), CatalogRelationship.id.asc())
                .all()
            )
        edges_by_layer: dict[int, list[CatalogRelationship]] = {}
        for edge in edges:
            edges_by_layer.setdefault(edge.target_object_id, []).append(edge)

        groups_by_layer: dict[int, list[CatalogPrerequisiteGroup]] = {}
        if self.enabled_source_ids:
            for group in (
                CatalogPrerequisiteGroup.query.filter(
                    CatalogPrerequisiteGroup.parent_group_id.is_(None),
                    CatalogPrerequisiteGroup.source_pack_id.in_(self.enabled_source_ids),
                )
                .order_by(CatalogPrerequisiteGroup.id.asc())
                .all()
            ):
                groups_by_layer.setdefault(group.target_object_id, []).append(group)

        result: dict[int, list[RequirementNode]] = {}
        for winner_id in sorted(self.effective):
            # An override inherits requirements only if it declares none itself.
            for layer in self._chain(winner_id, follow_replaces=False):
                layer_edges = edges_by_layer.get(layer, [])
                layer_groups = groups_by_layer.get(layer, [])
                if layer_edges or layer_groups:
                    result[winner_id] = self._roots_from(
                        winner_id, layer_edges, layer_groups, slug_by_type
                    )
                    break
            else:
                result[winner_id] = []
        return result

    def _leaf(self, object_id: int, kind: str, amount, dependent: int) -> RequirementLeaf | None:
        winner = self.winner_of(object_id)
        if winner is None or winner == dependent:
            return None  # requirement is not part of this campaign's catalog (or self-reference)
        entry = self.effective[winner]
        gating = kind not in NON_GATING_RELATIONSHIP_SLUGS and (
            self.type_by_id[entry["type_id"]].slug not in NON_GATING_TYPE_SLUGS
        )
        return RequirementLeaf(
            object_id=winner,
            name=entry["name"],
            kind=kind,
            gating=gating,
            amount=float(amount) if amount is not None else None,
        )

    def _roots_from(self, dependent, layer_edges, layer_groups, slug_by_type) -> list[RequirementNode]:
        roots: list[RequirementNode] = []
        grouped: dict[str, list[CatalogRelationship]] = {}
        group_order: list[str] = []
        seen: set[int] = set()
        for edge in layer_edges:
            kind = slug_by_type[edge.relationship_type_id]
            if edge.group_key:
                if edge.group_key not in grouped:
                    group_order.append(edge.group_key)
                grouped.setdefault(edge.group_key, []).append(edge)
                continue
            leaf = self._leaf(edge.source_object_id, kind, edge.amount, dependent)
            if leaf is None or leaf.object_id in seen:
                continue
            seen.add(leaf.object_id)
            roots.append(leaf)
        for key in group_order:
            members_edges = grouped[key]
            logic = next((e.group_logic for e in members_edges if e.group_logic), "AND")
            leaves: list[RequirementNode] = []
            for edge in members_edges:
                leaf = self._leaf(edge.source_object_id, slug_by_type[edge.relationship_type_id], edge.amount, dependent)
                if leaf is not None:
                    leaves.append(leaf)
            if leaves:
                roots.append(RequirementGroup(logic=logic, members=tuple(leaves)))
        for group in layer_groups:
            node = self._group_tree(group, slug_by_type, dependent)
            if node is not None:
                roots.append(node)
        return roots

    def _group_tree(self, group, slug_by_type, dependent: int, _depth: int = 0) -> RequirementNode | None:
        if _depth > 32:  # defensive: a corrupted self-referencing group must not recurse forever
            return None
        kind = slug_by_type.get(group.relationship_type_id, "prerequisite")
        members: list[RequirementNode] = []
        for member in groups_repo.list_members(group.id):
            if member.child_group_id is not None:
                child = groups_repo.get_group(member.child_group_id)
                node = self._group_tree(child, slug_by_type, dependent, _depth + 1) if child else None
                if node is not None:
                    members.append(node)
            elif member.catalog_object_id is not None:
                leaf = self._leaf(member.catalog_object_id, kind, member.amount, dependent)
                if leaf is not None:
                    members.append(leaf)
        if not members:
            return None
        return RequirementGroup(logic=group.logic, members=tuple(members))

    # ------------------------------------------------------ recorded state

    def _state(self) -> list[CampaignObjectState]:
        if self._state_rows is None:
            self._state_rows = CampaignObjectState.query.filter_by(campaign_id=self.campaign.id).all()
        return self._state_rows

    def queue_rows(self) -> list[CampaignResearchQueueItem]:
        if self._queue_rows is None:
            self._queue_rows = (
                CampaignResearchQueueItem.query.filter_by(campaign_id=self.campaign.id)
                .order_by(CampaignResearchQueueItem.position.asc(), CampaignResearchQueueItem.id.asc())
                .all()
            )
        return self._queue_rows

    def queue_ids(self) -> list[int]:
        """Queued object ids (effective winners), in queue order."""
        seen: set[int] = set()
        ordered: list[int] = []
        for row in self.queue_rows():
            winner = self.normalize(row.catalog_object_id)
            if winner not in seen:
                seen.add(winner)
                ordered.append(winner)
        return ordered

    def state_row(self, object_id: int) -> CampaignObjectState | None:
        winner = self.normalize(object_id)
        candidates = [r for r in self._state() if self.normalize(r.catalog_object_id) == winner]
        if not candidates:
            return None
        rank = {"completed": 3, "owned": 3, "in_progress": 2}
        return max(candidates, key=lambda r: (rank.get(r.status, 0), r.id))

    def _recorded(self) -> tuple[dict[int, str], set[int]]:
        recorded: dict[int, str] = {}
        overrides: set[int] = set()
        priority = {RECORDED_COMPLETED: 3, RECORDED_IN_PROGRESS: 2, RECORDED_PLANNED: 1, RECORDED_NONE: 0}

        def merge(object_id: int, status: str) -> None:
            winner = self.normalize(object_id)
            if priority[status] > priority[recorded.get(winner, RECORDED_NONE)]:
                recorded[winner] = status

        for row in self._state():
            if row.prerequisite_override:
                overrides.add(self.normalize(row.catalog_object_id))
            if row.status in SATISFIED_STATE_STATUSES:
                merge(row.catalog_object_id, RECORDED_COMPLETED)
            elif row.status == "in_progress":
                merge(row.catalog_object_id, RECORDED_IN_PROGRESS)
        for object_id in self.queue_ids():
            merge(object_id, RECORDED_PLANNED)
        for cell in CampaignAvengerCell.query.filter_by(campaign_id=self.campaign.id).all():
            if cell.facility_object_id is None:
                continue
            status = {
                "built": RECORDED_COMPLETED,
                "constructing": RECORDED_IN_PROGRESS,
                "planned": RECORDED_PLANNED,
            }.get(cell.build_state)
            if status:
                merge(cell.facility_object_id, status)
        return recorded, overrides

    def facts(self) -> dict[int, ObjectFacts]:
        if self._facts is None:
            recorded, overrides = self._recorded()
            facts: dict[int, ObjectFacts] = {}
            for object_id, entry in self.effective.items():
                slug = self.type_by_id[entry["type_id"]].slug
                category = category_of(slug)
                facts[object_id] = ObjectFacts(
                    object_id=object_id,
                    name=entry["name"],
                    type_slug=slug,
                    recorded=recorded.get(object_id, RECORDED_NONE),
                    derived=category == CATEGORY_UNLOCKABLE,
                    gating=category != CATEGORY_RESOURCE,
                    override_satisfied=object_id in overrides,
                )
            self._facts = facts
        return self._facts

    def statuses(self) -> dict[int, ObjectStatus]:
        if self._statuses is None:
            self._statuses = compute_object_statuses(self.facts(), self.requirement_map())
        return self._statuses

    def satisfied_ids(self) -> set[int]:
        return {o for o, st in self.statuses().items() if st.satisfied}

    def statuses_assuming_completed(self, extra_completed: set[int]) -> dict[int, ObjectStatus]:
        """Statuses if `extra_completed` were also done (queue simulation)."""
        facts = dict(self.facts())
        for object_id in extra_completed:
            info = facts.get(object_id)
            if info is not None and info.recorded != RECORDED_COMPLETED:
                facts[object_id] = ObjectFacts(
                    info.object_id, info.name, info.type_slug, RECORDED_COMPLETED,
                    info.derived, info.gating, info.override_satisfied,
                )
        return compute_object_statuses(facts, self.requirement_map())

    # ------------------------------------------------------------- decoration

    def ref(self, object_id: int, **extra) -> ObjectRef | None:
        winner = self.normalize(object_id)
        entry = self.effective.get(winner)
        if entry is None:
            return None
        base = self._refs.get(winner)
        if base is None:
            slug = self.type_by_id[entry["type_id"]].slug
            status = self.statuses().get(winner)
            base = ObjectRef(
                object_id=winner,
                name=entry["name"],
                type_slug=slug,
                type_name=self.type_by_id[entry["type_id"]].name,
                category=category_of(slug),
                status=status.status if status else STATUS_BLOCKED,
                status_label=status_label(slug, status.status if status else STATUS_BLOCKED),
                satisfied=bool(status and status.satisfied),
                requirements_met=bool(status and status.requirements_met),
                primary_asset_id=entry.get("primary_asset_id"),
                glyph=asset_service.fallback_glyph_for_type(self.type_by_id[entry["type_id"]]),
                source_name=self.source_name(entry.get("source_id")),
            )
            self._refs[winner] = base
        if not extra:
            return base
        return ObjectRef(**{**base.__dict__, **extra})


def describe_unmet(
    index: DependencyIndex,
    unmet: UnmetRequirement,
    *,
    wording: str = "recorded",
) -> Blocker:
    """Human wording for one unmet requirement, with the objects that would
    resolve it. `wording="recorded"` -> "X prerequisite not recorded as
    complete" (planner screens); `"incomplete"` -> "X incomplete" (queue)."""
    leaves = [leaf for leaf in unmet.leaves if leaf.gating]
    options = tuple(
        (leaf.object_id, leaf.name)
        for leaf in leaves
        if category_of(index.type_slug(leaf.object_id)) != CATEGORY_FACILITY
    )
    if len(leaves) == 1:
        leaf = leaves[0]
        is_facility = category_of(index.type_slug(leaf.object_id)) == CATEGORY_FACILITY
        if wording == "incomplete":
            text = f"{leaf.name} facility not built" if is_facility else f"{leaf.name} incomplete"
        else:
            noun = "facility" if is_facility else "prerequisite"
            text = f"{leaf.name} {noun} not recorded as complete"
        return Blocker(text, options)
    if wording == "incomplete":
        return Blocker(f"Needs one of {unmet.description}: none complete", options)
    return Blocker(f"None of {unmet.description} recorded as complete", options)
