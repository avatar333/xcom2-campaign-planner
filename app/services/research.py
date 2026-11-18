"""Research & Dependency Planner service (ADR-0021, FR-RSH-*).

Everything shown comes from the campaign's *effective catalog* and the generic
requirement graph (`app.services.dependency_index`); no research project, weapon
or facility is named anywhere in this module.

Campaign isolation (ADR-0010): every function takes the campaign and writes only
that campaign's `campaign_research_queue` / `campaign_object_state` rows. Catalog
rows are read, never changed (ADR-0002).

Facts vs strategy (ADR-0009): this module reports *facts* ("Magnetic Weapons
requires X"). Advice such as "research Magnetic Weapons early" belongs to the
`RecommendationProvider` seam at the bottom, which is intentionally empty.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from app.domain.dependency_graph import (
    STATUS_AVAILABLE,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_PLANNED,
    DependencyGraph,
    build_dependency_graph,
    direct_unlocks,
)
from app.domain.requirements import (
    RequirementGroup,
    RequirementLeaf,
    RequirementNode,
    evaluate_requirements,
    iter_leaves,
)
from app.extensions import db
from app.models import Campaign, CampaignResearchQueueItem
from app.services import campaign_progress
from app.services.dependency_index import (
    CATEGORY_PROJECT,
    PROJECT_TYPE_SLUGS,
    Blocker,
    DependencyIndex,
    ObjectRef,
    category_of,
    describe_unmet,
)

QUEUE_STATE_PLANNED = "planned"
QUEUE_STATE_IN_PROGRESS = "in_progress"
ACTIONABLE_PROJECT_STATUSES = (STATUS_AVAILABLE, STATUS_PLANNED)


class ResearchError(ValueError):
    """Raised for invalid research operations."""


# --------------------------------------------------------------- view data


@dataclass(frozen=True)
class RequirementEntry:
    """One line of a REQUIRES list. `logic` is "all" (a single required object),
    "any" (an OR: one of several alternatives) or "group" (a nested AND)."""

    logic: str
    refs: tuple[ObjectRef, ...]
    satisfied: bool
    gating: bool = True


@dataclass(frozen=True)
class ResearchCard:
    ref: ObjectRef
    category: str | None
    tags: tuple[str, ...]
    requires: tuple[RequirementEntry, ...]
    unlocks: tuple[ObjectRef, ...]
    in_queue: bool
    queue_position: int | None
    short_description: str | None


@dataclass(frozen=True)
class ResearchFilters:
    q: str = ""
    category: str = ""
    status: str = ""
    source_id: int | None = None
    type_slug: str = ""


@dataclass(frozen=True)
class ResearchBrowse:
    cards: tuple[ResearchCard, ...]
    categories: tuple[str, ...]
    sources: tuple[tuple[int, str], ...]
    types: tuple[tuple[str, str], ...]
    total_unfiltered: int
    counts: dict[str, int]


@dataclass(frozen=True)
class QueueItemView:
    position: int  # 1-based
    ref: ObjectRef
    state: str  # planned | in_progress
    category: str | None
    warnings: tuple[Blocker, ...]
    unlocks: tuple[ObjectRef, ...]

    @property
    def is_blocked(self) -> bool:
        return bool(self.warnings)


@dataclass(frozen=True)
class QueueSuggestion:
    order: tuple[int, ...]
    changed: bool
    moves: tuple[tuple[int, int, int], ...]  # (object_id, old_position, new_position), 1-based
    unresolved: tuple[Blocker, ...]  # blockers the queue cannot fix by reordering alone


@dataclass(frozen=True)
class QueueView:
    items: tuple[QueueItemView, ...]
    warnings_total: int
    suggestion: QueueSuggestion | None


@dataclass(frozen=True)
class ResearchDetail:
    ref: ObjectRef
    description: str | None
    category: str | None
    tags: tuple[str, ...]
    requires: tuple[RequirementEntry, ...]
    unlocks: tuple[ObjectRef, ...]
    blockers: tuple[Blocker, ...]
    can_start: bool
    in_queue: bool
    queue_position: int | None
    recorded_status: str
    completed_at: str | None
    completion_sequence: int | None
    attributes: dict


@dataclass(frozen=True)
class PathStep:
    order: int
    ref: ObjectRef
    actionable: bool
    optional: bool = False


@dataclass(frozen=True)
class PathAlternative:
    dependent: ObjectRef
    options: tuple[ObjectRef, ...]


@dataclass(frozen=True)
class UnlockPath:
    target: ObjectRef
    graph: DependencyGraph
    refs: dict[int, ObjectRef]
    satisfied: tuple[ObjectRef, ...]
    missing: tuple[ObjectRef, ...]
    actionable: tuple[ObjectRef, ...]
    blocked: tuple[ObjectRef, ...]
    mandatory_steps: tuple[PathStep, ...]
    optional_steps: tuple[PathStep, ...]
    alternatives: tuple[PathAlternative, ...]
    cycles: tuple[tuple[str, ...], ...]
    target_met: bool

    @property
    def outstanding_steps(self) -> int:
        return len(self.mandatory_steps)


@dataclass(frozen=True)
class GraphPayload:
    """JSON-ready Cytoscape elements (the only thing the front end renders)."""

    nodes: list[dict]
    edges: list[dict]


# -------------------------------------------------------------- helpers


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _entry_for(index: DependencyIndex, node: RequirementNode, satisfied: set[int]) -> RequirementEntry | None:
    if isinstance(node, RequirementLeaf):
        ref = index.ref(node.object_id, amount=node.amount, relationship=node.kind)
        if ref is None:
            return None
        return RequirementEntry("all", (ref,), satisfied=(not node.gating) or node.object_id in satisfied, gating=node.gating)
    refs = tuple(
        r
        for r in (
            index.ref(leaf.object_id, amount=leaf.amount, relationship=leaf.kind)
            for leaf in iter_leaves(node)
        )
        if r is not None
    )
    if not refs:
        return None
    met = evaluate_requirements([node], satisfied).is_met
    return RequirementEntry("any" if node.logic == "OR" else "group", refs, satisfied=met)


def _requires_entries(index: DependencyIndex, object_id: int) -> tuple[RequirementEntry, ...]:
    satisfied = index.satisfied_ids()
    entries: list[RequirementEntry] = []
    for root in index.roots_for(object_id):
        if isinstance(root, RequirementGroup) and root.logic == "AND":
            for member in root.members:  # an explicit AND is just more required items
                entry = _entry_for(index, member, satisfied)
                if entry is not None:
                    entries.append(entry)
            continue
        entry = _entry_for(index, root, satisfied)
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


def _unlock_refs(index: DependencyIndex, object_id: int, *, limit: int | None = None) -> tuple[ObjectRef, ...]:
    winner = index.normalize(object_id)
    refs: list[ObjectRef] = []
    for unlock in direct_unlocks(index.requirement_map(), winner):
        others = tuple(
            index.effective[o]["name"] for o in unlock.other_required if o in index.effective
        )
        ref = index.ref(unlock.object_id, is_alternative=unlock.is_alternative, other_required=others)
        if ref is not None:
            refs.append(ref)
    refs.sort(key=lambda r: (_unlock_rank(r), r.name.lower()))
    return tuple(refs[:limit] if limit else refs)


def _unlock_rank(ref: ObjectRef) -> int:
    return {"project": 0, "facility": 1, "action": 2, "unlockable": 3}.get(ref.category, 4)


def _research_category(index: DependencyIndex, object_id: int) -> str | None:
    entry = index.effective.get(index.normalize(object_id))
    if entry is None:
        return None
    value = (entry.get("attributes") or {}).get("research_category")
    return str(value) if value else None


def _project_object(index: DependencyIndex, object_id: int) -> int:
    winner = index.winner_of(object_id)
    if winner is None:
        raise ResearchError("That object is not part of this campaign's catalog.")
    if index.type_slug(winner) not in PROJECT_TYPE_SLUGS:
        raise ResearchError("Only research, autopsy, Proving Ground or technology projects can be queued.")
    return winner


# ----------------------------------------------------------------- browser


def browse_research(campaign: Campaign, filters: ResearchFilters | None = None) -> ResearchBrowse:
    filters = filters or ResearchFilters()
    index = DependencyIndex(campaign)
    queue_ids = index.queue_ids()
    positions = {o: i + 1 for i, o in enumerate(queue_ids)}
    entries = index.effective_of_type_slugs(PROJECT_TYPE_SLUGS)

    cards: list[ResearchCard] = []
    categories: set[str] = set()
    sources: dict[int, str] = {}
    types: dict[str, str] = {}
    counts = {STATUS_COMPLETED: 0, STATUS_IN_PROGRESS: 0, STATUS_PLANNED: 0, STATUS_AVAILABLE: 0, STATUS_BLOCKED: 0}
    needle = _norm(filters.q)
    for entry in entries:
        ref = index.ref(entry["id"])
        category = _research_category(index, entry["id"])
        if category:
            categories.add(category)
        if entry.get("source_id") is not None:
            sources[entry["source_id"]] = index.source_name(entry["source_id"]) or ""
        types[ref.type_slug] = ref.type_name
        counts[ref.status] = counts.get(ref.status, 0) + 1
        if filters.category and category != filters.category:
            continue
        if filters.status and ref.status != filters.status:
            continue
        if filters.source_id is not None and entry.get("source_id") != filters.source_id:
            continue
        if filters.type_slug and ref.type_slug != filters.type_slug:
            continue
        if needle:
            haystack = _norm(" ".join([entry["name"], entry.get("short_description") or "", category or "", ref.type_name]))
            if needle not in haystack:
                continue
        cards.append(
            ResearchCard(
                ref=ref,
                category=category,
                tags=index.tags_for(entry["id"]),
                requires=_requires_entries(index, entry["id"]),
                unlocks=_unlock_refs(index, entry["id"], limit=6),
                in_queue=entry["id"] in positions,
                queue_position=positions.get(entry["id"]),
                short_description=entry.get("short_description"),
            )
        )
    return ResearchBrowse(
        cards=tuple(cards),
        categories=tuple(sorted(categories)),
        sources=tuple(sorted(sources.items(), key=lambda kv: kv[1].lower())),
        types=tuple(sorted(types.items(), key=lambda kv: kv[1].lower())),
        total_unfiltered=len(entries),
        counts=counts,
    )


def get_research_detail(campaign: Campaign, object_id: int) -> ResearchDetail:
    index = DependencyIndex(campaign)
    winner = index.winner_of(object_id)
    if winner is None:
        raise ResearchError("That object is not part of this campaign's catalog.")
    ref = index.ref(winner)
    entry = index.effective[winner]
    queue_ids = index.queue_ids()
    row = index.state_row(winner)
    evaluation = evaluate_requirements(index.roots_for(winner), index.satisfied_ids())
    return ResearchDetail(
        ref=ref,
        description=entry.get("long_description") or entry.get("short_description"),
        category=_research_category(index, winner),
        tags=index.tags_for(winner),
        requires=_requires_entries(index, winner),
        unlocks=_unlock_refs(index, winner),
        blockers=tuple(describe_unmet(index, u) for u in evaluation.unmet),
        can_start=ref.category == CATEGORY_PROJECT and ref.requirements_met and not ref.satisfied,
        in_queue=winner in queue_ids,
        queue_position=(queue_ids.index(winner) + 1) if winner in queue_ids else None,
        recorded_status=ref.status,
        completed_at=row.completed_at if row is not None and ref.satisfied else None,
        completion_sequence=row.completion_sequence if row is not None and ref.satisfied else None,
        attributes=dict(entry.get("attributes") or {}),
    )


# ------------------------------------------------------------------- queue


def _queue_rows(campaign: Campaign) -> list[CampaignResearchQueueItem]:
    return (
        CampaignResearchQueueItem.query.filter_by(campaign_id=campaign.id)
        .order_by(CampaignResearchQueueItem.position.asc(), CampaignResearchQueueItem.id.asc())
        .all()
    )


def _renumber(rows: list[CampaignResearchQueueItem]) -> None:
    for position, row in enumerate(rows):
        row.position = position


def _canonical_rows(campaign: Campaign, index: DependencyIndex) -> list[CampaignResearchQueueItem]:
    """Queue rows with their object ids normalised to the ruleset winner; stale
    duplicates (two rows resolving to one object) are dropped."""
    rows = _queue_rows(campaign)
    seen: set[int] = set()
    kept: list[CampaignResearchQueueItem] = []
    for row in rows:
        winner = index.normalize(row.catalog_object_id)
        if winner in seen:
            db.session.delete(row)
            continue
        seen.add(winner)
        row.catalog_object_id = winner
        kept.append(row)
    return kept


def add_to_queue(campaign: Campaign, object_id: int, *, position: int | None = None) -> None:
    """Plan a project. Impossible orders and unmet prerequisites are allowed
    (reported as warnings); completed or already-queued projects are not."""
    index = DependencyIndex(campaign)
    winner = _project_object(index, object_id)
    if index.statuses()[winner].satisfied:
        raise ResearchError("That project is already completed for this campaign.")
    rows = _canonical_rows(campaign, index)
    if any(r.catalog_object_id == winner for r in rows):
        raise ResearchError("That project is already in the queue.")
    new_row = CampaignResearchQueueItem(campaign_id=campaign.id, catalog_object_id=winner, position=len(rows))
    db.session.add(new_row)
    if position is not None:
        rows.insert(max(0, min(position - 1, len(rows))), new_row)
    else:
        rows.append(new_row)
    _renumber(rows)
    db.session.commit()


def remove_from_queue(campaign: Campaign, object_id: int) -> None:
    index = DependencyIndex(campaign)
    winner = index.normalize(object_id)
    rows = _canonical_rows(campaign, index)
    target = next((r for r in rows if r.catalog_object_id == winner), None)
    if target is None:
        raise ResearchError("That project is not in the queue.")
    row = index.state_row(winner)
    if row is not None and row.status == "in_progress":
        campaign_progress.reset(campaign, winner, index=index, commit=False)
    db.session.delete(target)
    _renumber([r for r in rows if r is not target])
    db.session.commit()


def reorder_queue(campaign: Campaign, ordered_object_ids: list[int]) -> None:
    """Persist a new order. The list must contain exactly the queued projects,
    each once -- the server never guesses at a partial order."""
    index = DependencyIndex(campaign)
    rows = _canonical_rows(campaign, index)
    by_object = {r.catalog_object_id: r for r in rows}
    wanted = [index.normalize(o) for o in ordered_object_ids]
    if len(set(wanted)) != len(wanted) or set(wanted) != set(by_object):
        raise ResearchError("The new order must list every queued project exactly once.")
    _renumber([by_object[o] for o in wanted])
    db.session.commit()


def move_in_queue(campaign: Campaign, object_id: int, *, to_position: int | None = None, offset: int | None = None) -> None:
    """Keyboard/no-JS reordering: move to an absolute 1-based position, or by
    an offset (-1 = up one place, +1 = down one place)."""
    index = DependencyIndex(campaign)
    winner = index.normalize(object_id)
    rows = _canonical_rows(campaign, index)
    current = next((i for i, r in enumerate(rows) if r.catalog_object_id == winner), None)
    if current is None:
        raise ResearchError("That project is not in the queue.")
    if to_position is None and offset is None:
        raise ResearchError("Say where to move the project.")
    target = (to_position - 1) if to_position is not None else current + offset
    target = max(0, min(target, len(rows) - 1))
    row = rows.pop(current)
    rows.insert(target, row)
    _renumber(rows)
    db.session.commit()


def start_research(campaign: Campaign, object_id: int) -> None:
    """Mark a project in progress, queueing it first if needed. Not blocked by
    unmet prerequisites (the player may know better); it is flagged instead."""
    index = DependencyIndex(campaign)
    winner = _project_object(index, object_id)
    if index.statuses()[winner].satisfied:
        raise ResearchError("That project is already completed for this campaign.")
    rows = _canonical_rows(campaign, index)
    if not any(r.catalog_object_id == winner for r in rows):
        db.session.add(CampaignResearchQueueItem(campaign_id=campaign.id, catalog_object_id=winner, position=len(rows)))
        db.session.flush()
        _renumber(_queue_rows(campaign))
    campaign_progress.mark_in_progress(campaign, winner, index=index, commit=False)
    db.session.commit()


def complete_research(campaign: Campaign, object_id: int) -> None:
    """Record completion (with timestamp and order) and leave the queue."""
    index = DependencyIndex(campaign)
    winner = _project_object(index, object_id)
    campaign_progress.mark_completed(campaign, winner, index=index, commit=False)
    rows = _canonical_rows(campaign, index)
    remaining = [r for r in rows if r.catalog_object_id != winner]
    for row in rows:
        if row.catalog_object_id == winner:
            db.session.delete(row)
    _renumber(remaining)
    db.session.commit()


def reopen_research(campaign: Campaign, object_id: int) -> None:
    """Undo a completion/in-progress record (e.g. a mis-click)."""
    index = DependencyIndex(campaign)
    winner = _project_object(index, object_id)
    campaign_progress.reset(campaign, winner, index=index, commit=False)
    db.session.commit()


def _unmet_blockers(index: DependencyIndex, object_id: int, satisfied: set[int]) -> tuple[Blocker, ...]:
    evaluation = evaluate_requirements(index.roots_for(object_id), satisfied)
    return tuple(describe_unmet(index, unmet, wording="incomplete") for unmet in evaluation.unmet)


def get_queue_view(campaign: Campaign, *, with_suggestion: bool = False) -> QueueView:
    """The ordered queue, each item validated against what would be complete by
    its position (everything already done, plus the queue items before it)."""
    index = DependencyIndex(campaign)
    queue_ids = index.queue_ids()
    base_satisfied = index.satisfied_ids()
    items: list[QueueItemView] = []
    completed_before: set[int] = set()
    warnings_total = 0
    for position, object_id in enumerate(queue_ids, start=1):
        satisfied = (
            base_satisfied
            if not completed_before
            else {o for o, st in index.statuses_assuming_completed(completed_before).items() if st.satisfied}
        )
        warnings = _unmet_blockers(index, object_id, satisfied)
        warnings_total += 1 if warnings else 0
        row = index.state_row(object_id)
        in_progress = row is not None and row.status == "in_progress"
        items.append(
            QueueItemView(
                position=position,
                ref=index.ref(object_id),
                state=QUEUE_STATE_IN_PROGRESS if in_progress else QUEUE_STATE_PLANNED,
                category=_research_category(index, object_id),
                warnings=warnings,
                unlocks=_unlock_refs(index, object_id, limit=4),
            )
        )
        completed_before.add(object_id)
    suggestion = suggest_queue_order(campaign) if with_suggestion and queue_ids else None
    return QueueView(items=tuple(items), warnings_total=warnings_total, suggestion=suggestion)


def suggest_queue_order(campaign: Campaign) -> QueueSuggestion:
    """A valid prerequisite ordering of the *same* queued projects.

    Stable: an item only moves when something it needs is queued after it, so
    the player's own order is preserved wherever it is already valid. Nothing is
    changed here -- the caller applies the suggestion only if the user asks
    (`apply_queue_order`). Blockers that no queued project can resolve are
    reported separately: reordering cannot fix those."""
    index = DependencyIndex(campaign)
    queue_ids = index.queue_ids()
    base = index.satisfied_ids()
    queued = set(queue_ids)
    deps: dict[int, set[int]] = {}
    unresolved: list[Blocker] = []
    for object_id in queue_ids:
        needed: set[int] = set()
        for unmet in evaluate_requirements(index.roots_for(object_id), base).unmet:
            candidates = [leaf.object_id for leaf in unmet.leaves if leaf.gating and leaf.object_id in queued and leaf.object_id != object_id]
            if candidates:
                # For an OR, depend on the alternative the user already queued earliest.
                needed.add(min(candidates, key=lambda c: queue_ids.index(c)))
            else:
                unresolved.append(describe_unmet(index, unmet, wording="incomplete"))
        deps[object_id] = needed

    remaining = list(queue_ids)
    emitted: list[int] = []
    while remaining:
        pick = next((o for o in remaining if deps[o] <= set(emitted)), None)
        if pick is None:  # dependency cycle inside the queue: keep the user's order for the rest
            pick = remaining[0]
        remaining.remove(pick)
        emitted.append(pick)
    moves = tuple(
        (o, queue_ids.index(o) + 1, emitted.index(o) + 1) for o in emitted if queue_ids.index(o) != emitted.index(o)
    )
    seen_text: set[str] = set()
    unique_unresolved = []
    for blocker in unresolved:
        if blocker.text not in seen_text:
            seen_text.add(blocker.text)
            unique_unresolved.append(blocker)
    return QueueSuggestion(
        order=tuple(emitted),
        changed=emitted != queue_ids,
        moves=moves,
        unresolved=tuple(unique_unresolved),
    )


def apply_queue_order(campaign: Campaign, ordered_object_ids: list[int]) -> None:
    """Apply an order the user explicitly accepted (same validation as reorder)."""
    reorder_queue(campaign, ordered_object_ids)


# -------------------------------------------------------------- unlock path


def list_path_targets(campaign: Campaign, *, q: str = "", type_slug: str = "", limit: int = 60) -> list[ObjectRef]:
    """Objects the player can ask "how do I unlock this?" about."""
    index = DependencyIndex(campaign)
    needle = _norm(q)
    refs: list[ObjectRef] = []
    for entry in sorted(index.effective.values(), key=lambda e: (e["name"].lower(), e["id"])):
        ref = index.ref(entry["id"])
        if ref.category == "resource" or ref.type_slug == "avenger_room_state":
            continue
        if type_slug and ref.type_slug != type_slug:
            continue
        if needle and needle not in _norm(entry["name"] + " " + ref.type_name):
            continue
        refs.append(ref)
        if len(refs) >= limit:
            break
    return refs


def path_target_types(campaign: Campaign) -> list[tuple[str, str]]:
    index = DependencyIndex(campaign)
    found: dict[str, str] = {}
    for entry in index.effective.values():
        slug = index.type_by_id[entry["type_id"]].slug
        if category_of(slug) == "resource" or slug == "avenger_room_state":
            continue
        found[slug] = index.type_by_id[entry["type_id"]].name
    return sorted(found.items(), key=lambda kv: kv[1].lower())


def get_unlock_path(campaign: Campaign, object_id: int) -> UnlockPath:
    index = DependencyIndex(campaign)
    winner = index.winner_of(object_id)
    if winner is None:
        raise ResearchError("That object is not part of this campaign's catalog.")
    graph = build_dependency_graph(winner, index.facts(), index.requirement_map())

    refs: dict[int, ObjectRef] = {}
    for node in graph.nodes:
        if node.object_id is not None:
            ref = index.ref(node.object_id)
            if ref is not None:
                refs[node.object_id] = ref

    def many(ids: tuple[int, ...]) -> tuple[ObjectRef, ...]:
        return tuple(refs[i] for i in ids if i in refs)

    analysis = graph.analysis
    actionable = set(analysis.actionable_ids)
    return UnlockPath(
        target=refs[winner],
        graph=graph,
        refs=refs,
        satisfied=many(analysis.satisfied_ids),
        missing=many(analysis.missing_ids),
        actionable=many(analysis.actionable_ids),
        blocked=many(analysis.blocked_ids),
        mandatory_steps=tuple(
            PathStep(i + 1, refs[o], o in actionable) for i, o in enumerate(analysis.mandatory_steps) if o in refs
        ),
        optional_steps=tuple(PathStep(0, refs[o], o in actionable, optional=True) for o in analysis.optional_steps if o in refs),
        alternatives=tuple(
            PathAlternative(refs[a.dependent_id], many(a.member_ids))
            for a in analysis.alternatives
            if a.dependent_id in refs
        ),
        cycles=tuple(tuple(refs[o].name for o in cycle if o in refs) for cycle in graph.cycles),
        target_met=analysis.target_met,
    )


def build_graph_payload(
    path: UnlockPath, *, image_url, glyph_uri, detail_url
) -> GraphPayload:
    """Serialise an unlock path for the front end. The three callables keep this
    module free of Flask: `image_url(asset_id)`, `glyph_uri(glyph_svg, status)`
    and `detail_url(object_id)` are supplied by the route."""
    nodes: list[dict] = []
    for node in path.graph.nodes:
        if node.kind == "gate":
            nodes.append(
                {
                    "data": {
                        "id": node.node_id,
                        "kind": "gate",
                        "label": "ANY ONE OF" if node.logic == "OR" else "ALL OF",
                        "logic": node.logic,
                        "satisfied": node.satisfied,
                        "status": "gate",
                    }
                }
            )
            continue
        ref = path.refs[node.object_id]
        marker = {
            STATUS_COMPLETED: "\u2713 DONE",
            STATUS_IN_PROGRESS: "\u25b6 IN PROGRESS",
            STATUS_PLANNED: "\u25cb PLANNED",
            STATUS_AVAILABLE: "\u25c6 AVAILABLE",
            STATUS_BLOCKED: "\u2715 LOCKED",
        }[node.status]
        if ref.category == "resource":
            marker = "\u2022 NOT TRACKED"
        if node.is_target:
            marker = f"\u2605 TARGET \u00b7 {marker}"
        image = image_url(ref.primary_asset_id) if ref.primary_asset_id else glyph_uri(ref.glyph, node.status)
        nodes.append(
            {
                "data": {
                    "id": node.node_id,
                    "kind": "object",
                    "objectId": node.object_id,
                    "label": f"{ref.name}\n{ref.type_name}\n{marker}",
                    "name": ref.name,
                    "type": ref.type_name,
                    "status": node.status,
                    "statusLabel": ref.status_label,
                    "isTarget": node.is_target,
                    "isStep": node.is_step,
                    "satisfied": node.satisfied,
                    "image": image,
                    "url": detail_url(node.object_id),
                }
            }
        )
    edges = [
        {
            "data": {
                "id": f"e{i}",
                "source": edge.source,
                "target": edge.target,
                "relationship": edge.relationship or "",
                "label": (f"x{edge.amount:g}" if edge.amount else ""),
                "cycle": edge.is_cycle,
                "gating": edge.gating,
            }
        }
        for i, edge in enumerate(path.graph.edges)
    ]
    return GraphPayload(nodes=nodes, edges=edges)


# ---------------------------------------------------- recommendation seam


@dataclass(frozen=True)
class Recommendation:
    """Strategy advice, deliberately separate from facts (ADR-0009).

    `subject_object_id` is the catalog object the advice is about; `rationale`
    and `source` credit where the opinion comes from. Nothing in this phase
    produces recommendations, and nothing the planner shows as a *fact*
    (requirements, unlocks, blockers) is ever derived from one."""

    subject_object_id: int
    summary: str
    rationale: str = ""
    source: str = ""
    strategy_profile_slug: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)


class RecommendationProvider(Protocol):
    """Future seam: strategy profiles plug in here (ADR-0009, ADR-0021)."""

    def recommendations_for(self, campaign: Campaign, object_ids: list[int]) -> list[Recommendation]: ...


class NullRecommendationProvider:
    def recommendations_for(self, campaign: Campaign, object_ids: list[int]) -> list[Recommendation]:
        return []


_provider: RecommendationProvider = NullRecommendationProvider()


def get_recommendation_provider() -> RecommendationProvider:
    return _provider


def set_recommendation_provider(provider: RecommendationProvider) -> None:
    global _provider
    _provider = provider


def recommendations_for(campaign: Campaign, object_ids: list[int]) -> list[Recommendation]:
    """Advice about these objects (empty until a strategy provider is installed)."""
    return get_recommendation_provider().recommendations_for(campaign, object_ids)
