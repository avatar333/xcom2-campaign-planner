"""Pure-domain tests for build compatibility validation and summaries (ADR-0022)."""
from __future__ import annotations

from app.domain.builds import (
    BaseFacts,
    ComponentFacts,
    SlotSpec,
    check_compatibility,
    summarize_build,
    validate_build,
)

BASE = BaseFacts(1, "shotgun")


def comp(object_id, name="Comp", type_slug="weapon_upgrade", **kw):
    return ComponentFacts(object_id, name, type_slug, **kw)


def test_compatibility_is_unverified_with_no_data():
    assert check_compatibility(BASE, comp(5)).state == "unverified"


def test_compatibility_by_explicit_object_id():
    c = comp(5, compatible_object_ids=frozenset({1, 2}))
    assert check_compatibility(BASE, c).state == "compatible"
    assert check_compatibility(BaseFacts(99, "rifle"), c).state == "incompatible"


def test_compatibility_by_category():
    c = comp(5, compatible_categories=("shotgun", "rifle"))
    assert check_compatibility(BASE, c).state == "compatible"
    assert check_compatibility(BaseFacts(1, "sniper_rifle"), c).state == "incompatible"


def test_validate_rejects_unknown_slot():
    result = validate_build(BASE, [], {("weapon_upgrade", 0): comp(5)})
    assert not result.is_valid
    assert result.errors[0].code == "no_such_slot" and not result.errors[0].overridable


def test_validate_accepts_unverified_component_without_blocking():
    slots = [SlotSpec("weapon_upgrade", 0, "Upgrade 1", ("weapon_upgrade",))]
    result = validate_build(BASE, slots, {("weapon_upgrade", 0): comp(5)})
    assert result.is_valid
    assert result.unverified == [("weapon_upgrade", 0)] or result.unverified == (("weapon_upgrade", 0),)


def test_validate_rejects_wrong_component_type():
    slots = [SlotSpec("weapon_upgrade", 0, "Upgrade 1", ("weapon_upgrade",))]
    result = validate_build(BASE, slots, {("weapon_upgrade", 0): comp(5, type_slug="utility_item")})
    assert not result.is_valid and result.errors[0].code == "type_mismatch"


def test_validate_rejects_known_incompatible_component():
    slots = [SlotSpec("weapon_upgrade", 0, "Upgrade 1", ("weapon_upgrade",))]
    result = validate_build(BASE, slots, {("weapon_upgrade", 0): comp(5, compatible_categories=("sniper_rifle",))})
    assert not result.is_valid and result.errors[0].code == "incompatible"


def test_manual_override_accepts_an_incompatible_component():
    slots = [SlotSpec("weapon_upgrade", 0, "Upgrade 1", ("weapon_upgrade",))]
    key = ("weapon_upgrade", 0)
    bad = {key: comp(5, compatible_categories=("sniper_rifle",))}
    rejected = validate_build(BASE, slots, bad)
    assert not rejected.is_valid

    overridden = validate_build(BASE, slots, bad, overrides={key})
    assert overridden.is_valid
    assert overridden.overridden[0].code == "incompatible" and overridden.overridden[0].slot == key


def test_non_overridable_issues_are_never_accepted_even_with_override_flag():
    key = ("weapon_upgrade", 0)
    result = validate_build(BASE, [], {key: comp(5)}, overrides={key})
    assert not result.is_valid  # no_such_slot is not overridable


def test_exclusive_group_conflict():
    slots = [SlotSpec("weapon_upgrade", i, f"U{i}", ("weapon_upgrade",)) for i in range(2)]
    attachments = {
        ("weapon_upgrade", 0): comp(5, exclusive_group="sight"),
        ("weapon_upgrade", 1): comp(6, exclusive_group="sight"),
    }
    result = validate_build(BASE, slots, attachments)
    assert not result.is_valid and result.errors[0].code == "exclusive_conflict"


def test_duplicate_component_rejected_unless_explicitly_allowed():
    slots = [SlotSpec("weapon_upgrade", i, f"U{i}", ("weapon_upgrade",)) for i in range(2)]
    dup_blocked = {
        ("weapon_upgrade", 0): comp(5, allows_duplicates=False),
        ("weapon_upgrade", 1): comp(5, allows_duplicates=False),
    }
    assert not validate_build(BASE, slots, dup_blocked).is_valid

    dup_allowed = {
        ("weapon_upgrade", 0): comp(5, allows_duplicates=True),
        ("weapon_upgrade", 1): comp(5, allows_duplicates=True),
    }
    assert validate_build(BASE, slots, dup_allowed).is_valid

    dup_unknown = {
        ("weapon_upgrade", 0): comp(5, allows_duplicates=None),
        ("weapon_upgrade", 1): comp(5, allows_duplicates=None),
    }
    assert validate_build(BASE, slots, dup_unknown).is_valid  # unknown = allowed


def test_available_object_ids_rejects_objects_outside_the_campaign():
    slots = [SlotSpec("weapon_upgrade", 0, "U0", ("weapon_upgrade",))]
    result = validate_build(BASE, slots, {("weapon_upgrade", 0): comp(5)}, available_object_ids={1})
    assert not result.is_valid and result.errors[0].code == "not_in_campaign"


def test_empty_build_is_valid():
    assert validate_build(BASE, [SlotSpec("weapon_upgrade", 0, "U0", ("weapon_upgrade",))], {}).is_valid


# ------------------------------------------------------------------- summary


def test_summary_computes_known_arithmetic_only():
    summary = summarize_build(
        "Rifle",
        {"damage_min": 4, "damage_max": 6, "crit_chance": 10, "clip_size": 4},
        [("Scope", {"aim_bonus": 10}), ("Repeater", {"crit_bonus": 5})],
    )
    base_labels = {l.label: l.value for l in summary.base_lines}
    assert base_labels["Critical chance"] == "10" and base_labels["Clip size"] == "4"
    combined = {l.label: l.value for l in summary.combined}
    assert combined["Critical chance"] == "15"  # 10 base + 5 bonus, known arithmetic
    assert combined["Aim"] == "+10"  # no base attribute to add to -> shown as a delta, not invented
    component_labels = {l.label: l.value for l in summary.component_lines}
    assert component_labels["Scope"] == "Aim +10"


def test_summary_never_sums_free_text_effects():
    summary = summarize_build("Rifle", {}, [("Mystery Mod", {"effect_summary": "Does something unknown."})])
    assert summary.component_lines[0].value == "effect described"
    assert summary.component_lines[0].detail == "Does something unknown."
    assert not summary.combined  # nothing numeric to total
    assert any("descriptive only" in n for n in summary.notes)


def test_summary_reports_no_components():
    summary = summarize_build("Rifle", {"damage_min": 4}, [])
    assert any("No components" in n for n in summary.notes)


def test_summary_component_with_nothing_recorded():
    summary = summarize_build("Rifle", {}, [("Unknown Thing", {})])
    assert summary.component_lines[0].value == "no effect recorded"
