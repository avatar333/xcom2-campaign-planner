"""Equipment-build rules and computed summaries (ADR-0022).

Pure module: no Flask/SQLAlchemy/I/O and no knowledge of any game object.
Callers supply plain facts read from the catalog; this module decides what is
*known* to be compatible, what is *known* to conflict, and what is merely
*unverified* because the catalog has no data. It never invents arithmetic: a
build summary adds numbers only for attributes whose meaning is known.

Vocabulary
- A **slot** is typed (`slot_type`) and accepts certain catalog object types.
- An **issue** is a known problem. `overridable` issues may be accepted by the
  user ("manual compatibility override") and are then recorded as overrides.
- **Unverified** attachments have no compatibility data either way: they are
  allowed and flagged, never blocked (a mod with thin metadata stays usable).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

SlotKey = tuple[str, int]  # (slot_type, slot_index)

ISSUE_TYPE_MISMATCH = "type_mismatch"
ISSUE_INCOMPATIBLE = "incompatible"
ISSUE_EXCLUSIVE = "exclusive_conflict"
ISSUE_DUPLICATE = "duplicate"
ISSUE_NO_SUCH_SLOT = "no_such_slot"
ISSUE_NOT_AVAILABLE = "not_in_campaign"


@dataclass(frozen=True)
class SlotSpec:
    slot_type: str
    index: int
    label: str
    accepts: tuple[str, ...]  # catalog type slugs
    verified: bool = True  # False when the slot count is not in the catalog

    @property
    def key(self) -> SlotKey:
        return (self.slot_type, self.index)


@dataclass(frozen=True)
class BaseFacts:
    object_id: int
    category: str | None = None  # e.g. weapon_category


@dataclass(frozen=True)
class ComponentFacts:
    object_id: int
    name: str
    type_slug: str
    compatible_categories: tuple[str, ...] = ()
    compatible_object_ids: frozenset[int] = field(default_factory=frozenset)
    exclusive_group: str | None = None
    allows_duplicates: bool | None = None  # None = unknown = allowed


@dataclass(frozen=True)
class Issue:
    code: str
    slot: SlotKey
    message: str
    overridable: bool = True


@dataclass(frozen=True)
class Compatibility:
    """How one component relates to one base: known good, known bad, unknown."""

    state: str  # "compatible" | "incompatible" | "unverified"
    reason: str = ""


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[Issue, ...]  # unresolved, blocking
    overridden: tuple[Issue, ...]  # accepted by the user, to be recorded
    unverified: tuple[SlotKey, ...]  # attached with no compatibility data

    @property
    def is_valid(self) -> bool:
        return not self.errors


def check_compatibility(base: BaseFacts, component: ComponentFacts) -> Compatibility:
    """Known from catalog data only. Both an explicit allow-list match and a
    category match count as compatible; a non-empty allow-list that does not
    include the base (or category) is incompatible; no data is unverified."""
    has_category_data = bool(component.compatible_categories)
    has_object_data = bool(component.compatible_object_ids)
    if not has_category_data and not has_object_data:
        return Compatibility("unverified", "The catalog has no compatibility data for this component.")
    if base.object_id in component.compatible_object_ids:
        return Compatibility("compatible")
    if has_category_data and base.category and base.category in component.compatible_categories:
        return Compatibility("compatible")
    return Compatibility("incompatible", f"{component.name} is not listed as compatible with this base.")


def validate_build(
    base: BaseFacts,
    slots: Sequence[SlotSpec],
    attachments: Mapping[SlotKey, ComponentFacts],
    overrides: set[SlotKey] | frozenset[SlotKey] = frozenset(),
    *,
    available_object_ids: set[int] | None = None,
) -> ValidationResult:
    """Validate attachments against slots and known compatibility data."""
    spec_by_key = {slot.key: slot for slot in slots}
    errors: list[Issue] = []
    overridden: list[Issue] = []
    unverified: list[SlotKey] = []

    def raise_issue(issue: Issue) -> None:
        if issue.overridable and issue.slot in overrides:
            overridden.append(issue)
        else:
            errors.append(issue)

    for key in sorted(attachments):
        component = attachments[key]
        spec = spec_by_key.get(key)
        if spec is None:
            errors.append(Issue(ISSUE_NO_SUCH_SLOT, key, f"This base has no {key[0].replace('_', ' ')} slot #{key[1] + 1}.", overridable=False))
            continue
        if available_object_ids is not None and component.object_id not in available_object_ids:
            errors.append(Issue(ISSUE_NOT_AVAILABLE, key, f"{component.name} is not part of this campaign's content sources.", overridable=False))
            continue
        if component.type_slug not in spec.accepts:
            raise_issue(Issue(ISSUE_TYPE_MISMATCH, key, f"{component.name} is not a kind of component this slot accepts."))
        compat = check_compatibility(base, component)
        if compat.state == "incompatible":
            raise_issue(Issue(ISSUE_INCOMPATIBLE, key, compat.reason))
        elif compat.state == "unverified":
            unverified.append(key)

    seen_groups: dict[str, SlotKey] = {}
    seen_objects: dict[int, SlotKey] = {}
    for key in sorted(attachments):
        component = attachments[key]
        if component.exclusive_group:
            first = seen_groups.get(component.exclusive_group)
            if first is not None:
                raise_issue(Issue(ISSUE_EXCLUSIVE, key, f"{component.name} cannot be combined with another '{component.exclusive_group}' component."))
            else:
                seen_groups[component.exclusive_group] = key
        first_same = seen_objects.get(component.object_id)
        if first_same is not None and component.allows_duplicates is False:
            raise_issue(Issue(ISSUE_DUPLICATE, key, f"{component.name} cannot be attached more than once."))
        seen_objects.setdefault(component.object_id, key)

    return ValidationResult(tuple(errors), tuple(overridden), tuple(unverified))


# ------------------------------------------------------------------- summary


@dataclass(frozen=True)
class SummaryLine:
    label: str
    value: str
    detail: str = ""


@dataclass(frozen=True)
class BuildSummary:
    base_lines: tuple[SummaryLine, ...]
    component_lines: tuple[SummaryLine, ...]
    combined: tuple[SummaryLine, ...]
    notes: tuple[str, ...]


# attribute key on the component -> (label, base attribute it modifies | None)
NUMERIC_BONUSES = {
    "aim_bonus": ("Aim", None),
    "crit_bonus": ("Critical chance", "crit_chance"),
    "clip_size_bonus": ("Clip size", "clip_size"),
}

BASE_ATTRIBUTE_LABELS = (
    ("damage_min", "Damage (min)"),
    ("damage_max", "Damage (max)"),
    ("crit_chance", "Critical chance"),
    ("clip_size", "Clip size"),
    ("armor_points", "Armor points"),
    ("utility_slots", "Utility slots"),
    ("upgrade_slots", "Upgrade slots"),
)


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: float) -> str:
    return f"{int(value)}" if float(value).is_integer() else f"{value:g}"


def summarize_build(
    base_name: str,
    base_attributes: Mapping[str, object],
    components: Sequence[tuple[str, Mapping[str, object]]],
) -> BuildSummary:
    """Show base facts, each component's recorded effect, and *only* numeric
    totals whose arithmetic is known (`NUMERIC_BONUSES`). Free-text effects are
    displayed, never parsed or summed."""
    base_lines = tuple(
        SummaryLine(label, _fmt(n))
        for key, label in BASE_ATTRIBUTE_LABELS
        if (n := _number(base_attributes.get(key))) is not None
    )
    component_lines: list[SummaryLine] = []
    totals: dict[str, float] = {}
    notes: list[str] = []
    for name, attrs in components:
        parts = []
        for key, (label, _target) in NUMERIC_BONUSES.items():
            n = _number(attrs.get(key))
            if n is not None:
                totals[key] = totals.get(key, 0) + n
                parts.append(f"{label} {'+' if n >= 0 else ''}{_fmt(n)}")
        effect = attrs.get("effect_summary")
        detail = str(effect) if effect else ""
        if parts or detail:
            component_lines.append(SummaryLine(name, ", ".join(parts) if parts else "effect described", detail))
        else:
            component_lines.append(SummaryLine(name, "no effect recorded"))
    combined: list[SummaryLine] = []
    for key, total in totals.items():
        label, target = NUMERIC_BONUSES[key]
        base_value = _number(base_attributes.get(target)) if target else None
        if target and base_value is not None:
            combined.append(SummaryLine(label, _fmt(base_value + total), f"{_fmt(base_value)} base {'+' if total >= 0 else ''}{_fmt(total)}"))
        else:
            combined.append(SummaryLine(label, f"{'+' if total >= 0 else ''}{_fmt(total)}", "from components"))
    if any(not (any(k in attrs for k in NUMERIC_BONUSES)) and attrs.get("effect_summary") for _n, attrs in components):
        notes.append("Some component effects are descriptive only and are not added into the totals.")
    if not components:
        notes.append(f"No components attached to {base_name}.")
    return BuildSummary(base_lines, tuple(component_lines), tuple(combined), tuple(notes))
