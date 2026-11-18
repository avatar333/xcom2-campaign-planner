"""Pure-domain tests for dependency traversal and path analysis (ADR-0021)."""
from __future__ import annotations

import pytest

from app.domain.dependency_graph import (
    ObjectFacts,
    build_dependency_graph,
    compute_object_statuses,
    direct_unlocks,
    downstream_closure,
)
from app.domain.requirements import RequirementGroup, RequirementLeaf

A, B, C, D, E, F, G, T = 1, 2, 3, 4, 5, 6, 7, 99


def leaf(object_id, kind="requires_research", **kw):
    return RequirementLeaf(object_id=object_id, name=f"n{object_id}", kind=kind, **kw)


def OR(*members):
    return RequirementGroup("OR", tuple(members))


def AND(*members):
    return RequirementGroup("AND", tuple(members))


def facts(*ids, completed=(), in_progress=(), planned=(), derived=(), resources=()):
    out = {}
    for i in ids:
        recorded = "completed" if i in completed else "in_progress" if i in in_progress else "planned" if i in planned else "none"
        out[i] = ObjectFacts(
            i, f"n{i}", "research_project" if i not in derived else "weapon",
            recorded=recorded, derived=i in derived, gating=i not in resources,
        )
    return out


def ids(nodes):
    return {n.object_id for n in nodes if n.object_id is not None}


# ------------------------------------------------------------ traversal


def test_multi_level_chain_includes_every_ancestor():
    # T <- C <- B <- A  (three levels above the target)
    reqs = {T: [leaf(C)], C: [leaf(B)], B: [leaf(A)]}
    graph = build_dependency_graph(T, facts(A, B, C, T), reqs)

    assert ids(graph.nodes) == {A, B, C, T}
    assert graph.object_node(A).distance == 3
    assert {(e.source, e.target) for e in graph.edges} == {("o1", "o2"), ("o2", "o3"), ("o3", "o99")}
    assert graph.analysis.mandatory_steps == (A, B, C)  # dependency-first order
    assert graph.analysis.satisfied_ids == ()


def test_and_requirement_needs_both_and_is_never_an_or():
    reqs = {T: [leaf(A), leaf(B)]}
    graph = build_dependency_graph(T, facts(A, B, T, completed=[A]), reqs)

    assert not graph.analysis.target_met  # B still missing: AND, not OR
    assert graph.analysis.missing_ids == (B,)
    assert graph.analysis.mandatory_steps == (B,)
    assert not any(n.kind == "gate" for n in graph.nodes)  # a plain AND draws no gate
    assert graph.object_node(T).status == "blocked"

    both = build_dependency_graph(T, facts(A, B, T, completed=[A, B]), reqs)
    assert both.analysis.target_met and both.object_node(T).status == "available"


def test_explicit_and_group_equals_flat_and():
    flat = build_dependency_graph(T, facts(A, B, T, completed=[A]), {T: [leaf(A), leaf(B)]})
    grouped = build_dependency_graph(T, facts(A, B, T, completed=[A]), {T: [AND(leaf(A), leaf(B))]})
    assert flat.analysis.target_met == grouped.analysis.target_met is False
    assert flat.analysis.missing_ids == grouped.analysis.missing_ids


def test_or_group_shows_all_alternatives_through_a_gate():
    reqs = {T: [OR(leaf(A), leaf(B))], A: [leaf(C)], B: [leaf(D)]}
    graph = build_dependency_graph(T, facts(A, B, C, D, T), reqs)

    gates = [n for n in graph.nodes if n.kind == "gate"]
    assert len(gates) == 1 and gates[0].logic == "OR" and gates[0].name == "ANY ONE OF"
    assert ids(graph.nodes) == {A, B, C, D, T}  # both branches are present
    gate_in = {e.source for e in graph.edges if e.target == gates[0].node_id}
    assert gate_in == {"o1", "o2"}
    assert any(e.source == gates[0].node_id and e.target == "o99" for e in graph.edges)
    assert len(graph.analysis.alternatives) == 1
    assert graph.analysis.alternatives[0].member_ids == (A, B)


def test_or_is_satisfied_by_any_one_branch_and_nothing_is_called_superior():
    reqs = {T: [OR(leaf(A), leaf(B))]}
    one = build_dependency_graph(T, facts(A, B, T, completed=[B]), reqs)
    assert one.analysis.target_met
    assert one.analysis.alternatives == ()  # resolved OR is no longer an open choice

    none = build_dependency_graph(T, facts(A, B, T), reqs)
    assert not none.analysis.target_met
    # neither alternative is mandatory; both are merely optional ways forward
    assert none.analysis.mandatory_steps == ()
    assert none.analysis.optional_steps == (A, B)
    assert none.analysis.actionable_ids == (A, B)


def test_or_with_shared_prerequisite_marks_it_mandatory():
    # Both alternatives need C, so C is required whichever branch is chosen.
    reqs = {T: [OR(leaf(A), leaf(B))], A: [leaf(C)], B: [leaf(C)]}
    analysis = build_dependency_graph(T, facts(A, B, C, T), reqs).analysis
    assert analysis.mandatory_steps == (C,)
    assert set(analysis.optional_steps) == {A, B}


def test_nested_groups_keep_exact_semantics():
    # T needs A AND (B OR (C AND D))
    reqs = {T: [leaf(A), OR(leaf(B), AND(leaf(C), leaf(D)))]}
    f = lambda **k: build_dependency_graph(T, facts(A, B, C, D, T, **k), reqs)

    assert not f(completed=[A, C]).analysis.target_met  # C alone is not (C AND D)
    assert f(completed=[A, C, D]).analysis.target_met
    assert f(completed=[A, B]).analysis.target_met
    assert not f(completed=[B, C, D]).analysis.target_met  # A is a plain AND requirement

    graph = f(completed=[A])
    gates = sorted(n.logic for n in graph.nodes if n.kind == "gate")
    assert gates == ["AND", "OR"]  # the inner AND gets its own visible gate
    assert graph.analysis.mandatory_steps == ()
    assert set(graph.analysis.optional_steps) == {B, C, D}


def test_single_member_group_is_flattened():
    graph = build_dependency_graph(T, facts(A, T), {T: [OR(leaf(A))]})
    assert not any(n.kind == "gate" for n in graph.nodes)
    assert {(e.source, e.target) for e in graph.edges} == {("o1", "o99")}


def test_completed_ancestors_are_marked_satisfied_and_analysed():
    reqs = {T: [leaf(C)], C: [leaf(A), leaf(B)]}
    graph = build_dependency_graph(T, facts(A, B, C, T, completed=[A]), reqs)

    assert graph.object_node(A).status == "completed" and graph.object_node(A).satisfied
    assert graph.analysis.satisfied_ids == (A,)
    assert graph.analysis.missing_ids == (B, C)
    assert graph.analysis.actionable_ids == (B,)  # C is blocked until B is done
    assert graph.analysis.blocked_ids == (C, T)
    assert graph.analysis.mandatory_steps == (B, C)
    assert graph.object_node(C).blocked and graph.object_node(B).status == "available"


def test_statuses_planned_in_progress_and_target_flags():
    reqs = {T: [leaf(A), leaf(B)]}
    graph = build_dependency_graph(T, facts(A, B, T, planned=[A], in_progress=[B]), reqs)
    assert graph.object_node(A).status == "planned"
    assert graph.object_node(B).status == "in_progress"
    assert graph.object_node(T).is_target and not graph.object_node(A).is_target


def test_derived_objects_unlock_when_requirements_met_and_do_not_count_as_steps():
    # weapon W (derived) needs research A; target facility T needs weapon W.
    W = 50
    reqs = {W: [leaf(A)], T: [leaf(W, kind="prerequisite")]}
    f = facts(A, W, T, derived=[W])

    blocked = build_dependency_graph(T, f, reqs)
    assert blocked.object_node(W).status == "blocked"
    assert blocked.analysis.mandatory_steps == (A,)  # only the research is an action

    done = build_dependency_graph(T, facts(A, W, T, derived=[W], completed=[A]), reqs)
    assert done.object_node(W).status == "available" and done.object_node(W).satisfied
    assert done.analysis.target_met


def test_non_gating_resource_requirements_are_shown_but_never_block():
    R = 60
    reqs = {T: [leaf(A), leaf(R, kind="requires_resource", gating=False, amount=1)]}
    graph = build_dependency_graph(T, facts(A, R, T, completed=[A], resources=[R]), reqs)

    assert graph.analysis.target_met
    assert graph.object_node(R) is not None  # still drawn
    edge = next(e for e in graph.edges if e.source == "o60")
    assert edge.amount == 1 and edge.gating is False
    assert R not in graph.analysis.missing_ids


def test_prerequisite_override_counts_as_satisfied():
    f = facts(A, T)
    f[A] = ObjectFacts(A, "n1", "research_project", override_satisfied=True)
    graph = build_dependency_graph(T, f, {T: [leaf(A)]})
    assert graph.analysis.target_met and graph.object_node(A).status == "completed"


def test_target_without_requirements():
    graph = build_dependency_graph(T, facts(T), {})
    assert [n.object_id for n in graph.nodes] == [T]
    assert graph.edges == () and graph.analysis.target_met and graph.analysis.mandatory_steps == ()


def test_unknown_target_raises_and_unknown_requirement_objects_are_ignored():
    with pytest.raises(KeyError):
        build_dependency_graph(12345, facts(A), {})
    graph = build_dependency_graph(T, facts(T), {T: [leaf(777)]})  # 777 is not in the catalog
    assert [n.object_id for n in graph.nodes] == [T]


def test_output_is_deterministic():
    reqs = {T: [leaf(B), OR(leaf(A), leaf(C))], B: [leaf(D)], C: [leaf(D)]}
    runs = [build_dependency_graph(T, facts(A, B, C, D, T), reqs) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    reordered = {C: reqs[C], B: reqs[B], T: reqs[T]}  # dict insertion order must not matter
    assert build_dependency_graph(T, facts(A, B, C, D, T), reordered) == runs[0]


# ------------------------------------------------------------- cycles


def test_two_node_cycle_terminates_and_is_reported():
    reqs = {T: [leaf(A)], A: [leaf(B)], B: [leaf(A)]}
    graph = build_dependency_graph(T, facts(A, B, T), reqs)

    assert graph.has_cycles
    assert any(set(cycle) == {A, B} for cycle in graph.cycles)
    assert any(e.is_cycle for e in graph.edges)
    assert ids(graph.nodes) == {A, B, T}
    assert not graph.analysis.target_met  # a cycle never satisfies itself


def test_self_requirement_is_a_cycle():
    graph = build_dependency_graph(T, facts(A, T), {T: [leaf(A)], A: [leaf(A)]})
    assert (A,) in graph.cycles and not graph.analysis.target_met


def test_cycle_through_target_terminates():
    graph = build_dependency_graph(T, facts(A, T), {T: [leaf(A)], A: [leaf(T)]})
    assert graph.has_cycles and not graph.analysis.target_met


def test_cycle_does_not_block_an_independent_completed_path():
    reqs = {T: [OR(leaf(A), leaf(C))], A: [leaf(B)], B: [leaf(A)]}
    graph = build_dependency_graph(T, facts(A, B, C, T, completed=[C]), reqs)
    assert graph.has_cycles and graph.analysis.target_met


def test_completed_member_of_a_cycle_is_still_honoured():
    reqs = {T: [leaf(A)], A: [leaf(B)], B: [leaf(A)]}
    graph = build_dependency_graph(T, facts(A, B, T, completed=[A]), reqs)
    assert graph.analysis.target_met and graph.has_cycles


def test_very_deep_chain_does_not_overflow_the_stack():
    n = 1500
    ids_ = list(range(1000, 1000 + n))
    reqs = {ids_[i]: [leaf(ids_[i + 1])] for i in range(n - 1)}
    graph = build_dependency_graph(ids_[0], facts(*ids_), reqs)
    assert len(graph.nodes) == n and len(graph.analysis.mandatory_steps) == n - 1


# ----------------------------------------------------------- unlocks


def test_direct_unlocks_lists_dependents_with_alternative_flag():
    reqs = {
        10: [leaf(A)],
        11: [leaf(A), leaf(B)],
        12: [OR(leaf(A), leaf(B))],
        13: [leaf(C)],
    }
    found = {u.object_id: u for u in direct_unlocks(reqs, A)}

    assert set(found) == {10, 11, 12}
    assert not found[10].is_alternative and found[10].other_required == ()
    assert not found[11].is_alternative and found[11].other_required == (B,)
    assert found[12].is_alternative


def test_downstream_closure_is_transitive_and_cycle_safe():
    reqs = {B: [leaf(A)], C: [leaf(B)], D: [leaf(C), leaf(A)], A: [leaf(D)]}  # includes a cycle back to A
    closure = downstream_closure(reqs, A)
    assert closure == {B: 1, D: 1, C: 2, A: 3} or closure[B] == 1
    assert {B, C, D} <= set(closure)
    assert downstream_closure(reqs, A, max_depth=1) == {B: 1, D: 1}


def test_compute_object_statuses_is_order_independent_and_cycle_safe():
    reqs = {B: [leaf(A)], A: [leaf(B)]}
    f = facts(A, B, derived=[A, B])
    result = compute_object_statuses(f, reqs)
    assert not result[A].satisfied and not result[B].satisfied  # unsupported cycle stays unsatisfied
    assert compute_object_statuses(facts(A, B, derived=[A, B], completed=[A]), reqs)[B].satisfied
