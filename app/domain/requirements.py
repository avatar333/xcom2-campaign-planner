"""Generic requirement-tree evaluation (ARCHITECTURE.md §6, ADR-0018).

Pure module. A requirement is either a leaf (one catalog object that must be
satisfied) or a group (AND/OR of children). Nothing here knows what any game
object *is*; callers pass in which object ids are currently satisfied for the
campaign. The result explains *why* something is unmet so the UI can show
blockers without preventing planning.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RequirementLeaf:
    object_id: int
    name: str
    kind: str = "prerequisite"  # relationship type slug the leaf came from
    # Non-gating leaves are *shown* (e.g. a corpse or resource cost) but never
    # block anything: the app does not simulate stockpiles (ADR-0021).
    gating: bool = True
    amount: float | None = None


@dataclass(frozen=True)
class RequirementGroup:
    logic: str  # "AND" | "OR"
    members: tuple[RequirementNode, ...] = field(default_factory=tuple)


RequirementNode = RequirementLeaf | RequirementGroup


@dataclass(frozen=True)
class UnmetRequirement:
    """One blocker: either a single leaf, or an OR-group none of whose
    alternatives is satisfied (`alternatives` lists them all)."""

    description: str
    leaves: tuple[RequirementLeaf, ...]


@dataclass(frozen=True)
class RequirementEvaluation:
    is_met: bool
    unmet: tuple[UnmetRequirement, ...]


def _is_satisfied(node: RequirementNode, satisfied_ids: frozenset[int]) -> bool:
    if isinstance(node, RequirementLeaf):
        return (not node.gating) or node.object_id in satisfied_ids
    results = [_is_satisfied(member, satisfied_ids) for member in node.members]
    if not results:
        return True
    return any(results) if node.logic == "OR" else all(results)


def _leaves(node: RequirementNode) -> tuple[RequirementLeaf, ...]:
    if isinstance(node, RequirementLeaf):
        return (node,)
    collected: list[RequirementLeaf] = []
    for member in node.members:
        collected.extend(_leaves(member))
    return tuple(collected)


def _describe(node: RequirementNode) -> str:
    if isinstance(node, RequirementLeaf):
        return node.name
    joiner = " or " if node.logic == "OR" else " and "
    parts = [_describe(member) for member in node.members]
    text = joiner.join(parts)
    return f"({text})" if len(parts) > 1 else text


def _collect_unmet(node: RequirementNode, satisfied_ids: frozenset[int]) -> list[UnmetRequirement]:
    if _is_satisfied(node, satisfied_ids):
        return []
    if isinstance(node, RequirementLeaf):
        return [UnmetRequirement(description=node.name, leaves=(node,))]
    if node.logic == "AND":
        unmet: list[UnmetRequirement] = []
        for member in node.members:
            unmet.extend(_collect_unmet(member, satisfied_ids))
        return unmet
    return [UnmetRequirement(description=_describe(node), leaves=_leaves(node))]


def node_is_met(node: RequirementNode, satisfied_ids: set[int] | frozenset[int]) -> bool:
    """Whether one requirement node (leaf or group) is satisfied."""
    return _is_satisfied(node, frozenset(satisfied_ids))


def iter_leaves(node: RequirementNode) -> tuple[RequirementLeaf, ...]:
    """Every leaf beneath `node`, in authored order (includes all OR alternatives)."""
    return _leaves(node)


def evaluate_requirements(
    roots: list[RequirementNode], satisfied_ids: set[int] | frozenset[int]
) -> RequirementEvaluation:
    """Evaluate independent root requirements (implicitly AND-ed together)."""
    satisfied = frozenset(satisfied_ids)
    unmet: list[UnmetRequirement] = []
    for root in roots:
        unmet.extend(_collect_unmet(root, satisfied))
    return RequirementEvaluation(is_met=not unmet, unmet=tuple(unmet))
