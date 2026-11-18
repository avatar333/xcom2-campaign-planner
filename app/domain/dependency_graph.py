"""Dependency-graph traversal and path analysis (ADR-0021).

Pure module: no Flask, no SQLAlchemy, no I/O, and no knowledge of any game
object. Callers pass in plain facts about catalog objects and each object's
requirement expression (AND/OR/nested groups, `app.domain.requirements`); this
module returns a deterministic graph that contains *every* ancestor needed to
reach a target -- all levels, all alternatives -- plus an analysis of what the
campaign already has and what is still missing.

Semantics (the part that must never be gotten wrong):
- The roots of an object's requirement list are AND-ed.
- A group's members are combined with the group's own logic (AND or OR);
  groups nest arbitrarily.
- `A requires B AND C` is never rendered or evaluated as `B OR C`: OR only ever
  comes from an explicit OR group, and alternatives are drawn through a visible
  gate node.

Cycles in corrupted/modded data are detected and reported; traversal always
terminates and cyclic edges never count as satisfying anything.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.domain.requirements import (
    RequirementLeaf,
    RequirementNode,
    evaluate_requirements,
    iter_leaves,
    node_is_met,
)

# Recorded campaign progress for an object (what the player has told us).
RECORDED_COMPLETED = "completed"
RECORDED_IN_PROGRESS = "in_progress"
RECORDED_PLANNED = "planned"
RECORDED_NONE = "none"

# Derived display status of an object in a campaign.
STATUS_COMPLETED = "completed"
STATUS_IN_PROGRESS = "in_progress"
STATUS_PLANNED = "planned"
STATUS_AVAILABLE = "available"
STATUS_BLOCKED = "blocked"
STATUS_GATE = "gate"

GRAPH_STATUSES = (STATUS_COMPLETED, STATUS_IN_PROGRESS, STATUS_PLANNED, STATUS_AVAILABLE, STATUS_BLOCKED)


@dataclass(frozen=True)
class ObjectFacts:
    """What the traversal needs to know about one catalog object."""

    object_id: int
    name: str
    type_slug: str
    recorded: str = RECORDED_NONE
    # True for things the player does not "do" but simply receives once their
    # requirements are met (weapons, armour, items): satisfied by derivation.
    derived: bool = False
    # False for objects that never gate anything (e.g. resources).
    gating: bool = True
    # The player asserted this requirement is met (`prerequisite_override`).
    override_satisfied: bool = False

    @property
    def is_step(self) -> bool:
        """A step is something the player must act on to progress."""
        return self.gating and not self.derived


@dataclass(frozen=True)
class GraphNode:
    node_id: str  # "o<object_id>" or "g<dependent>_<path>"
    kind: str  # "object" | "gate"
    name: str
    status: str
    satisfied: bool
    blocked: bool = False
    is_target: bool = False
    is_step: bool = False
    distance: int = 0
    object_id: int | None = None
    type_slug: str | None = None
    logic: str | None = None  # gates: "AND" | "OR"
    missing_requirements: int = 0


@dataclass(frozen=True)
class GraphEdge:
    source: str  # the requirement
    target: str  # the dependent (arrow points requirement -> dependent)
    relationship: str | None = None
    amount: float | None = None
    is_cycle: bool = False
    gating: bool = True


@dataclass(frozen=True)
class AlternativeGroup:
    """An unresolved OR: satisfy any one of `member_ids` for `dependent_id`."""

    dependent_id: int
    member_ids: tuple[int, ...]


@dataclass(frozen=True)
class PathAnalysis:
    satisfied_ids: tuple[int, ...]
    missing_ids: tuple[int, ...]  # unsatisfied ancestors (all alternatives)
    actionable_ids: tuple[int, ...]  # unsatisfied steps whose requirements are met now
    blocked_ids: tuple[int, ...]  # unsatisfied objects with unmet requirements
    mandatory_steps: tuple[int, ...]  # needed on EVERY path, dependency-ordered
    optional_steps: tuple[int, ...]  # only needed if a particular alternative is chosen
    alternatives: tuple[AlternativeGroup, ...]
    target_met: bool

    @property
    def outstanding_mandatory(self) -> int:
        return len(self.mandatory_steps)

    @property
    def has_alternatives(self) -> bool:
        return bool(self.alternatives)


@dataclass(frozen=True)
class DependencyGraph:
    target_id: int
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    cycles: tuple[tuple[int, ...], ...]
    analysis: PathAnalysis

    def node(self, node_id: str) -> GraphNode:
        return next(n for n in self.nodes if n.node_id == node_id)

    def object_node(self, object_id: int) -> GraphNode | None:
        return next((n for n in self.nodes if n.object_id == object_id), None)

    @property
    def has_cycles(self) -> bool:
        return bool(self.cycles)


@dataclass(frozen=True)
class Unlock:
    """Something that lists `via_id` among its requirements."""

    object_id: int
    is_alternative: bool  # `via_id` sits inside an OR group (one of several ways)
    other_required: tuple[int, ...] = field(default_factory=tuple)  # co-requirements (AND)


# ----------------------------------------------------------------- helpers


def _sorted_roots(roots: Sequence[RequirementNode]) -> list[RequirementNode]:
    return list(roots)  # authored order is preserved; it is already deterministic


def _flatten(node: RequirementNode) -> tuple[RequirementLeaf, ...]:
    return iter_leaves(node)


def _object_leaves(roots: Sequence[RequirementNode]) -> list[RequirementLeaf]:
    leaves: list[RequirementLeaf] = []
    for root in roots:
        leaves.extend(_flatten(root))
    return leaves


# ------------------------------------------------------------ reverse lookup


def direct_unlocks(requirements: Mapping[int, Sequence[RequirementNode]], object_id: int) -> list[Unlock]:
    """Objects that directly list `object_id` as a requirement, deterministic
    (by object id). `is_alternative` is True when it is one option of an OR."""
    found: dict[int, Unlock] = {}

    def scan(node: RequirementNode, dependent: int, *, in_or: bool, siblings: tuple[int, ...]) -> None:
        if isinstance(node, RequirementLeaf):
            if node.object_id == object_id:
                others = tuple(i for i in siblings if i != object_id)
                existing = found.get(dependent)
                if existing is None or (existing.is_alternative and not in_or):
                    found[dependent] = Unlock(dependent, in_or, others)
            return
        is_or = node.logic == "OR"
        sibling_ids = tuple(
            leaf.object_id for leaf in node.members if isinstance(leaf, RequirementLeaf) and leaf.gating
        )
        for member in node.members:
            scan(member, dependent, in_or=in_or or is_or, siblings=() if is_or else sibling_ids)

    for dependent in sorted(requirements):
        roots = requirements[dependent]
        top_leaf_ids = tuple(r.object_id for r in roots if isinstance(r, RequirementLeaf) and r.gating)
        for root in roots:
            scan(root, dependent, in_or=False, siblings=top_leaf_ids)
    return [found[key] for key in sorted(found)]


def downstream_closure(
    requirements: Mapping[int, Sequence[RequirementNode]], object_id: int, *, max_depth: int | None = None
) -> dict[int, int]:
    """Everything that (transitively) depends on `object_id`, as
    `{object_id: distance}`. Cycle-safe breadth-first search."""
    dependents_of: dict[int, set[int]] = {}
    for dependent, roots in requirements.items():
        for leaf in _object_leaves(roots):
            dependents_of.setdefault(leaf.object_id, set()).add(dependent)
    distance: dict[int, int] = {}
    queue: deque[tuple[int, int]] = deque([(object_id, 0)])
    seen = {object_id}
    while queue:
        current, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        for dependent in sorted(dependents_of.get(current, ())):
            if dependent in seen:
                continue
            seen.add(dependent)
            distance[dependent] = depth + 1
            queue.append((dependent, depth + 1))
    return distance


# ------------------------------------------------------------ main traversal


def build_dependency_graph(
    target_id: int,
    facts: Mapping[int, ObjectFacts],
    requirements: Mapping[int, Sequence[RequirementNode]],
) -> DependencyGraph:
    """Return the complete, deterministic dependency graph needed to reach
    `target_id` given the campaign's recorded progress."""
    if target_id not in facts:
        raise KeyError(f"Unknown target object {target_id}")

    # -- 1. Reachability: every object that could contribute, any alternative.
    distance: dict[int, int] = {target_id: 0}
    queue: deque[int] = deque([target_id])
    while queue:
        current = queue.popleft()
        for leaf in _object_leaves(requirements.get(current, ())):
            if leaf.object_id in distance or leaf.object_id not in facts:
                continue
            distance[leaf.object_id] = distance[current] + 1
            queue.append(leaf.object_id)
    reachable = set(distance)

    # -- 2. Cycle detection (iterative DFS; back edges are flagged, not followed).
    cycles, cycle_edges, postorder = _find_cycles(target_id, reachable, requirements)

    # -- 3. Satisfaction by least fixed point (cycle-safe, order independent).
    statuses = compute_object_statuses(facts, requirements, subset=reachable)
    satisfied: set[int] = {o for o, st in statuses.items() if st.satisfied}
    met: dict[int, bool] = {o: st.requirements_met for o, st in statuses.items()}

    # -- 4. Nodes and edges (with gate nodes for groups).
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    def object_node_id(object_id: int) -> str:
        return f"o{object_id}"

    for obj in sorted(reachable, key=lambda o: (distance[o], facts[o].name.lower(), o)):
        info = facts[obj]
        status = statuses[obj].status
        missing = _count_unmet_leaves(requirements.get(obj, ()), satisfied)
        nodes[object_node_id(obj)] = GraphNode(
            node_id=object_node_id(obj),
            kind="object",
            name=info.name,
            status=status,
            satisfied=obj in satisfied,
            blocked=(not met[obj]) and obj not in satisfied and info.gating,
            is_target=obj == target_id,
            is_step=info.is_step,
            distance=distance[obj],
            object_id=obj,
            type_slug=info.type_slug,
            missing_requirements=missing,
        )

    def add_requirement(
        node: RequirementNode, dependent_node: str, dependent_id: int, path: str, parent_logic: str
    ) -> None:
        if isinstance(node, RequirementLeaf):
            if node.object_id not in facts:
                return
            edges.append(
                GraphEdge(
                    source=object_node_id(node.object_id),
                    target=dependent_node,
                    relationship=node.kind,
                    amount=node.amount,
                    is_cycle=(node.object_id, dependent_id) in cycle_edges,
                    gating=node.gating,
                )
            )
            return
        members = [m for m in node.members if not (isinstance(m, RequirementLeaf) and m.object_id not in facts)]
        if not members:
            return
        if len(members) == 1:
            add_requirement(members[0], dependent_node, dependent_id, path, parent_logic)
            return
        if node.logic == "AND" and parent_logic == "AND":
            for index, member in enumerate(members):
                add_requirement(member, dependent_node, dependent_id, f"{path}_{index}", "AND")
            return
        gate_id = f"g{dependent_id}_{path}"
        gate_met = node_is_met(node, frozenset(satisfied))
        nodes[gate_id] = GraphNode(
            node_id=gate_id,
            kind="gate",
            name="ANY ONE OF" if node.logic == "OR" else "ALL OF",
            status=STATUS_GATE,
            satisfied=gate_met,
            blocked=not gate_met,
            distance=distance[dependent_id],
            logic=node.logic,
            missing_requirements=0 if gate_met else _count_unmet_leaves([node], satisfied),
        )
        edges.append(GraphEdge(source=gate_id, target=dependent_node, relationship="group"))
        for index, member in enumerate(members):
            add_requirement(member, gate_id, dependent_id, f"{path}_{index}", node.logic)

    for obj in sorted(reachable):
        for index, root in enumerate(_sorted_roots(requirements.get(obj, ()))):
            add_requirement(root, object_node_id(obj), obj, str(index), "AND")

    # Deduplicate identical edges, keep deterministic order.
    unique_edges = sorted(
        {(e.target, e.source, e.relationship or "", e.amount if e.amount is not None else -1, e.is_cycle, e.gating): e for e in edges}.values(),
        key=lambda e: (e.target, e.source, e.relationship or ""),
    )
    ordered_nodes = sorted(
        nodes.values(), key=lambda n: (n.distance, 0 if n.kind == "object" else 1, n.name.lower(), n.node_id)
    )

    analysis = _analyze(target_id, facts, requirements, reachable, satisfied, met, postorder, cycle_edges)
    return DependencyGraph(
        target_id=target_id,
        nodes=tuple(ordered_nodes),
        edges=tuple(unique_edges),
        cycles=tuple(cycles),
        analysis=analysis,
    )


@dataclass(frozen=True)
class ObjectStatus:
    """Campaign status of one object, identical on every screen."""

    status: str
    satisfied: bool  # counts as "done" when it appears as someone's requirement
    requirements_met: bool


def compute_object_statuses(
    facts: Mapping[int, ObjectFacts],
    requirements: Mapping[int, Sequence[RequirementNode]],
    *,
    subset: set[int] | None = None,
) -> dict[int, ObjectStatus]:
    """Status of every object (or just `subset`) given recorded progress.

    Satisfaction is the *least fixed point*: recorded-complete (or overridden)
    objects are satisfied; a derived object (weapon, armour, item ...) becomes
    satisfied once all its requirements are; nothing else is ever inferred. A
    dependency cycle with no external support therefore stays unsatisfied, and
    the computation always terminates.
    """
    ids = sorted(facts if subset is None else (o for o in subset if o in facts))
    scope = set(ids)
    satisfied: set[int] = {
        o for o in ids if facts[o].recorded == RECORDED_COMPLETED or facts[o].override_satisfied
    }
    dependents_of: dict[int, set[int]] = {}
    for obj in ids:
        for leaf in _object_leaves(requirements.get(obj, ())):
            if leaf.object_id in scope:
                dependents_of.setdefault(leaf.object_id, set()).add(obj)
    # Worklist: an object is re-examined only when one of its requirements
    # becomes satisfied, so long chains cost O(n), not O(n^2).
    pending: deque[int] = deque(o for o in ids if o not in satisfied and facts[o].derived)
    queued = set(pending)
    while pending:
        obj = pending.popleft()
        queued.discard(obj)
        if obj in satisfied:
            continue
        if evaluate_requirements(list(requirements.get(obj, ())), satisfied).is_met:
            satisfied.add(obj)
            for dependent in sorted(dependents_of.get(obj, ())):
                if dependent not in satisfied and facts[dependent].derived and dependent not in queued:
                    pending.append(dependent)
                    queued.add(dependent)
    result: dict[int, ObjectStatus] = {}
    for obj in ids:
        info = facts[obj]
        is_met = evaluate_requirements(list(requirements.get(obj, ())), satisfied).is_met
        result[obj] = ObjectStatus(
            status=_status_of(info, is_met, obj in satisfied),
            satisfied=obj in satisfied,
            requirements_met=is_met,
        )
    return result


def _status_of(info: ObjectFacts, requirements_met: bool, satisfied: bool) -> str:
    if info.recorded == RECORDED_COMPLETED or info.override_satisfied:
        return STATUS_COMPLETED
    if info.recorded == RECORDED_IN_PROGRESS:
        return STATUS_IN_PROGRESS
    if satisfied:  # derived unlock whose requirements are all met
        return STATUS_AVAILABLE
    if info.recorded == RECORDED_PLANNED:
        return STATUS_PLANNED
    return STATUS_AVAILABLE if requirements_met else STATUS_BLOCKED


def _count_unmet_leaves(roots: Sequence[RequirementNode], satisfied: set[int]) -> int:
    return len(evaluate_requirements(list(roots), satisfied).unmet)


def _find_cycles(
    target_id: int,
    reachable: set[int],
    requirements: Mapping[int, Sequence[RequirementNode]],
) -> tuple[list[tuple[int, ...]], set[tuple[int, int]], list[int]]:
    """Iterative DFS from the target over `dependent -> requirement` links.
    Returns (cycles, back_edges, postorder). A back edge is
    `(requirement, dependent)` whose requirement is currently being expanded
    further up the stack; `postorder` lists requirements before their
    dependents (back edges ignored), so bottom-up analyses need no recursion."""
    adjacency: dict[int, list[int]] = {}
    for obj in reachable:
        seen: set[int] = set()
        ordered: list[int] = []
        for leaf in _object_leaves(requirements.get(obj, ())):
            if leaf.object_id in reachable and leaf.object_id not in seen:
                seen.add(leaf.object_id)
                ordered.append(leaf.object_id)
        adjacency[obj] = sorted(ordered)

    cycles: list[tuple[int, ...]] = []
    back_edges: set[tuple[int, int]] = set()
    postorder: list[int] = []
    state: dict[int, int] = {}  # 1 = on stack, 2 = done
    stack_nodes: list[int] = []

    for root in [target_id, *sorted(reachable - {target_id})]:
        if state.get(root):
            continue
        work: list[tuple[int, int]] = [(root, 0)]
        state[root] = 1
        stack_nodes.append(root)
        while work:
            current, index = work.pop()
            neighbours = adjacency.get(current, [])
            if index < len(neighbours):
                work.append((current, index + 1))
                nxt = neighbours[index]
                if state.get(nxt) == 1:
                    back_edges.add((nxt, current))
                    start = stack_nodes.index(nxt)
                    cycle = tuple(stack_nodes[start:])
                    if cycle not in cycles:
                        cycles.append(cycle)
                elif not state.get(nxt):
                    state[nxt] = 1
                    stack_nodes.append(nxt)
                    work.append((nxt, 0))
            else:
                state[current] = 2
                stack_nodes.pop()
                postorder.append(current)
    return cycles, back_edges, postorder


# ---------------------------------------------------------------- analysis


def _analyze(
    target_id: int,
    facts: Mapping[int, ObjectFacts],
    requirements: Mapping[int, Sequence[RequirementNode]],
    reachable: set[int],
    satisfied: set[int],
    met: Mapping[int, bool],
    postorder: Sequence[int],
    cycle_edges: set[tuple[int, int]],
) -> PathAnalysis:
    ancestors = reachable - {target_id}
    satisfied_ids = tuple(sorted((o for o in ancestors if o in satisfied), key=lambda o: (facts[o].name.lower(), o)))
    missing_ids = tuple(sorted((o for o in ancestors if o not in satisfied and facts[o].gating), key=lambda o: (facts[o].name.lower(), o)))
    actionable = tuple(
        sorted(
            (o for o in reachable if o not in satisfied and facts[o].is_step and met[o]),
            key=lambda o: (facts[o].name.lower(), o),
        )
    )
    blocked = tuple(
        sorted(
            (o for o in reachable if o not in satisfied and facts[o].gating and not met[o]),
            key=lambda o: (facts[o].name.lower(), o),
        )
    )

    frozen_satisfied = frozenset(satisfied)
    # need_of[o]: the steps that must still be done to obtain `o` regardless of
    # which alternative is chosen (including `o` itself when it is a step).
    # Computed bottom-up over the DFS post-order, so there is no recursion
    # over the (possibly very deep) object graph.
    need_of: dict[int, frozenset[int]] = {}
    roots_need: dict[int, frozenset[int]] = {}

    def object_need(obj: int, dependent: int) -> frozenset[int]:
        if obj not in facts or obj in satisfied or not facts[obj].gating:
            return frozenset()
        if (obj, dependent) in cycle_edges or obj not in need_of:
            return frozenset({obj}) if facts[obj].is_step else frozenset()
        return need_of[obj]

    def need_node(node: RequirementNode, dependent: int) -> frozenset[int]:
        # Group nesting is shallow, so recursing over the expression is safe.
        if node_is_met(node, frozen_satisfied):
            return frozenset()
        if isinstance(node, RequirementLeaf):
            return object_need(node.object_id, dependent)
        child_needs = [need_node(m, dependent) for m in node.members if not node_is_met(m, frozen_satisfied)]
        if node.logic == "OR":
            if not child_needs:
                return frozenset()
            common = set(child_needs[0])
            for other in child_needs[1:]:
                common &= other
            return frozenset(common)
        union: set[int] = set()
        for part in child_needs:
            union |= part
        return frozenset(union)

    for obj in postorder:
        gathered: set[int] = set()
        for root in requirements.get(obj, ()):
            gathered |= need_node(root, obj)
        roots_need[obj] = frozenset(gathered)
        own = frozenset({obj}) if (facts[obj].is_step and obj not in satisfied) else frozenset()
        need_of[obj] = own | roots_need[obj] if obj not in satisfied else frozenset()

    mandatory_set = frozenset(o for o in roots_need.get(target_id, frozenset()) if o != target_id)
    missing_steps = {o for o in missing_ids if facts[o].is_step}
    optional = tuple(
        sorted(missing_steps - mandatory_set, key=lambda o: (facts[o].name.lower(), o))
    )
    mandatory = _topological(mandatory_set, facts, lambda o: roots_need.get(o, frozenset()) & mandatory_set)

    alternatives: list[AlternativeGroup] = []

    def collect_alternatives(dependent: int, node: RequirementNode) -> None:
        if isinstance(node, RequirementLeaf):
            return
        if node.logic == "OR" and not node_is_met(node, frozenset(satisfied)):
            members = tuple(
                sorted({leaf.object_id for leaf in iter_leaves(node) if leaf.object_id in facts})
            )
            if len(members) > 1:
                alternatives.append(AlternativeGroup(dependent, members))
        for member in node.members:
            collect_alternatives(dependent, member)

    for obj in sorted(reachable):
        if obj in satisfied:
            continue
        for root in requirements.get(obj, ()):
            collect_alternatives(obj, root)

    return PathAnalysis(
        satisfied_ids=satisfied_ids,
        missing_ids=missing_ids,
        actionable_ids=actionable,
        blocked_ids=blocked,
        mandatory_steps=tuple(mandatory),
        optional_steps=optional,
        alternatives=tuple(alternatives),
        target_met=met[target_id] or target_id in satisfied,
    )


def _topological(
    members: frozenset[int] | set[int],
    facts: Mapping[int, ObjectFacts],
    dependencies_of,
) -> list[int]:
    """Dependency-first order, ties broken by name then id. Cycle-safe."""
    ordered: list[int] = []
    done: set[int] = set()
    for start in sorted(members, key=lambda o: (facts[o].name.lower(), o)):
        if start in done:
            continue
        stack: list[tuple[int, list[int]]] = [
            (start, sorted(dependencies_of(start) - {start}, key=lambda o: (facts[o].name.lower(), o), reverse=True))
        ]
        on_stack = {start}
        while stack:
            current, pending = stack[-1]
            if pending:
                nxt = pending.pop()
                if nxt in done or nxt in on_stack:
                    continue
                on_stack.add(nxt)
                stack.append(
                    (nxt, sorted(dependencies_of(nxt) - {nxt}, key=lambda o: (facts[o].name.lower(), o), reverse=True))
                )
            else:
                stack.pop()
                on_stack.discard(current)
                if current not in done:
                    done.add(current)
                    ordered.append(current)
    return ordered
