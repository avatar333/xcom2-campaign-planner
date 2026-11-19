"""Reusable equipment builds: weapon, armour, and any future kind (ADR-0022).

A build is `Build -> base catalog object -> typed slots -> attached catalog
objects`, owned by one campaign. Which slots exist, what they accept, what is
compatible and what is mutually exclusive all come from catalog data
(`upgrade_slots` / `utility_slots` / a `build_slots` JSON attribute, the
`compatible_with` relationship, `compatible_weapon_categories`,
`exclusive_group`, `allows_duplicates_in_build`) so a mod's weapons, armour,
upgrades and slot types work with no code change.

Campaign isolation (ADR-0010): every function takes the campaign and reads or
writes only rows whose `campaign_id` matches. Catalog rows are only read.

Assignments reference the *shared* build (no copy): editing a build is seen by
every soldier using it, and a build in use cannot be deleted by accident.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.domain.builds import (
    BaseFacts,
    BuildSummary,
    Compatibility,
    ComponentFacts,
    SlotKey,
    SlotSpec,
    ValidationResult,
    check_compatibility,
    summarize_build,
    validate_build,
)
from app.extensions import db
from app.models import (
    Campaign,
    CampaignBuild,
    CampaignBuildSlot,
    CampaignSoldier,
    CampaignSoldierBuild,
    CatalogRelationship,
    CatalogRelationshipType,
)
from app.services.dependency_index import DependencyIndex, ObjectRef

STATE_AVAILABLE = "available"
STATE_PLANNED = "planned"
AVAILABILITY_LABELS = {STATE_AVAILABLE: "Available", STATE_PLANNED: "PLANNED / NOT YET AVAILABLE"}


class BuildError(ValueError):
    """Raised for invalid build operations."""


class BuildValidationError(BuildError):
    def __init__(self, messages: list[str]) -> None:
        super().__init__("; ".join(messages))
        self.messages = messages


class BuildInUseError(BuildError):
    def __init__(self, soldiers: list[str]) -> None:
        super().__init__("This build is assigned to: " + ", ".join(soldiers) + ".")
        self.soldiers = soldiers


# ----------------------------------------------------------------- registry


@dataclass(frozen=True)
class SlotRule:
    slot_type: str
    label: str
    count_attribute: str
    accepts: tuple[str, ...]


@dataclass(frozen=True)
class BuildTypeSpec:
    key: str
    label: str
    plural: str
    base_type_slugs: tuple[str, ...]
    slot_rules: tuple[SlotRule, ...]
    assignment_slots: tuple[str, ...] = ("primary",)
    unknown_slot_count: int = 2  # offered (flagged unverified) when the catalog gives no count


BUILD_TYPES: dict[str, BuildTypeSpec] = {
    "weapon": BuildTypeSpec(
        key="weapon",
        label="Weapon Build",
        plural="Weapon Builds",
        base_type_slugs=("weapon",),
        slot_rules=(SlotRule("weapon_upgrade", "Weapon upgrade", "upgrade_slots", ("weapon_upgrade",)),),
    ),
    "armor": BuildTypeSpec(
        key="armor",
        label="Armour Build",
        plural="Armour Builds",
        base_type_slugs=("armor",),
        slot_rules=(SlotRule("utility", "Utility", "utility_slots", ("utility_item", "ammo")),),
    ),
}


def get_build_type(key: str) -> BuildTypeSpec:
    spec = BUILD_TYPES.get(key)
    if spec is None:
        raise BuildError(f"Unknown build type '{key}'.")
    return spec


# ------------------------------------------------------------------ context


class BuildContext(DependencyIndex):
    """The dependency index plus build-specific catalog reads."""

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(campaign)
        self._compat: dict[int, set[int]] | None = None

    def compat_map(self) -> dict[int, set[int]]:
        if self._compat is None:
            compat: dict[int, set[int]] = {}
            rel_type = CatalogRelationshipType.query.filter_by(slug="compatible_with").first()
            if rel_type is not None and self.enabled_source_ids:
                rows = CatalogRelationship.query.filter(
                    CatalogRelationship.relationship_type_id == rel_type.id,
                    CatalogRelationship.source_pack_id.in_(self.enabled_source_ids),
                ).all()
                for row in rows:
                    a, b = self.normalize(row.source_object_id), self.normalize(row.target_object_id)
                    compat.setdefault(a, set()).add(b)
                    compat.setdefault(b, set()).add(a)
            self._compat = compat
        return self._compat

    def attrs(self, object_id: int) -> dict:
        entry = self.effective.get(self.normalize(object_id))
        return dict(entry.get("attributes") or {}) if entry else {}

    def base_facts(self, object_id: int) -> BaseFacts:
        winner = self.normalize(object_id)
        category = self.attrs(winner).get("weapon_category")
        return BaseFacts(winner, str(category) if category else None)

    def component_facts(self, object_id: int) -> ComponentFacts:
        winner = self.normalize(object_id)
        attrs = self.attrs(winner)
        categories = tuple(c.strip() for c in str(attrs.get("compatible_weapon_categories") or "").split(",") if c.strip())
        dup = attrs.get("allows_duplicates_in_build")
        return ComponentFacts(
            object_id=winner,
            name=self.effective[winner]["name"],
            type_slug=self.type_slug(winner),
            compatible_categories=categories,
            compatible_object_ids=frozenset(self.compat_map().get(winner, set())),
            exclusive_group=(str(attrs["exclusive_group"]) if attrs.get("exclusive_group") else None),
            allows_duplicates=bool(dup) if dup is not None else None,
        )


def _int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def slot_specs(ctx: BuildContext, build_type: str, base_id: int) -> list[SlotSpec]:
    """Typed slots of a base object, entirely from its catalog attributes."""
    spec = get_build_type(build_type)
    attrs = ctx.attrs(base_id)
    slots: list[SlotSpec] = []
    for rule in spec.slot_rules:
        count = _int(attrs.get(rule.count_attribute))
        verified = count is not None
        if count is None:
            count = spec.unknown_slot_count
        for i in range(max(count, 0)):
            slots.append(SlotSpec(rule.slot_type, i, f"{rule.label} {i + 1}", rule.accepts, verified))
    extra = attrs.get("build_slots")
    if extra:
        try:
            for item in json.loads(extra) if isinstance(extra, str) else extra:
                for i in range(max(int(item.get("count", 1)), 0)):
                    label = item.get("label") or item["slot_type"].replace("_", " ").title()
                    slots.append(SlotSpec(item["slot_type"], i, f"{label} {i + 1}", tuple(item.get("accepts", ())), True))
        except (ValueError, KeyError, TypeError):
            pass  # malformed mod metadata must never break the editor
    return slots


# ---------------------------------------------------------------- view data


@dataclass(frozen=True)
class ComponentOption:
    ref: ObjectRef
    compatibility: Compatibility
    slot_types: tuple[str, ...]
    quality: str | None = None


@dataclass(frozen=True)
class ComponentView:
    slot: SlotSpec
    ref: ObjectRef
    compatibility: Compatibility
    is_override: bool


@dataclass(frozen=True)
class BuildView:
    id: int
    name: str
    build_type: str
    type_label: str
    purpose: str | None
    notes: str | None
    base: ObjectRef
    components: tuple[ComponentView, ...]
    empty_slots: tuple[SlotSpec, ...]
    validation: ValidationResult
    availability: str
    missing: tuple[ObjectRef, ...]
    summary: BuildSummary
    assigned: tuple[tuple[int, str], ...] = field(default_factory=tuple)

    @property
    def availability_label(self) -> str:
        return AVAILABILITY_LABELS[self.availability]

    @property
    def is_available(self) -> bool:
        return self.availability == STATE_AVAILABLE

    @property
    def component_refs(self) -> tuple[ObjectRef, ...]:
        return tuple(c.ref for c in self.components)

    @property
    def overrides(self) -> tuple[ComponentView, ...]:
        return tuple(c for c in self.components if c.is_override)


# ------------------------------------------------------------------ options


def list_base_options(campaign: Campaign, build_type: str, *, ctx: BuildContext | None = None) -> list[ObjectRef]:
    spec = get_build_type(build_type)
    ctx = ctx or BuildContext(campaign)
    entries = ctx.effective_of_type_slugs(spec.base_type_slugs)
    entries.sort(key=lambda e: (e.get("tier") or 0, e["name"].lower()))
    return [ctx.ref(e["id"]) for e in entries]


def list_component_options(
    campaign: Campaign, build_type: str, base_id: int, *, ctx: BuildContext | None = None
) -> tuple[list[SlotSpec], dict[str, list[ComponentOption]]]:
    """Slots of the chosen base plus, per slot type, every component the
    campaign can see, annotated with compatibility *for that base*."""
    ctx = ctx or BuildContext(campaign)
    base = _require_base(ctx, build_type, base_id)
    specs = slot_specs(ctx, build_type, base)
    accepted: dict[str, tuple[str, ...]] = {s.slot_type: s.accepts for s in specs}
    base_facts = ctx.base_facts(base)
    options: dict[str, list[ComponentOption]] = {}
    for slot_type, accepts in accepted.items():
        entries = ctx.effective_of_type_slugs(accepts)
        entries.sort(key=lambda e: (e["name"].lower(), e["id"]))
        options[slot_type] = [
            ComponentOption(
                ref=ctx.ref(e["id"]),
                compatibility=check_compatibility(base_facts, ctx.component_facts(e["id"])),
                slot_types=(slot_type,),
                quality=(e.get("attributes") or {}).get("quality"),
            )
            for e in entries
        ]
    return specs, options


# --------------------------------------------------------------- operations


def _require_base(ctx: BuildContext, build_type: str, base_id: int) -> int:
    spec = get_build_type(build_type)
    winner = ctx.winner_of(base_id)
    if winner is None or ctx.type_slug(winner) not in spec.base_type_slugs:
        raise BuildError(f"Choose a {spec.label.lower().replace(' build', '')} available to this campaign.")
    return winner


def _clean_name(campaign: Campaign, build_type: str, name: str, *, exclude_id: int | None = None) -> str:
    name = (name or "").strip()
    if not name:
        raise BuildValidationError(["Give the build a name."])
    if len(name) > 100:
        raise BuildValidationError(["Build names must be 100 characters or fewer."])
    clash = CampaignBuild.query.filter_by(campaign_id=campaign.id, build_type=build_type, name=name).first()
    if clash is not None and clash.id != exclude_id:
        raise BuildValidationError([f"This campaign already has a {get_build_type(build_type).label.lower()} named '{name}'."])
    return name


def _attachments(ctx: BuildContext, attachments: dict[SlotKey, int]) -> dict[SlotKey, ComponentFacts]:
    facts: dict[SlotKey, ComponentFacts] = {}
    for key, object_id in attachments.items():
        if object_id is None:
            continue
        winner = ctx.winner_of(object_id)
        if winner is None:
            raise BuildValidationError(["One of the chosen components is not part of this campaign's content sources."])
        facts[key] = ctx.component_facts(winner)
    return facts


def _validate(ctx, build_type, base, attachments, overrides) -> tuple[list[SlotSpec], dict[SlotKey, ComponentFacts], ValidationResult]:
    specs = slot_specs(ctx, build_type, base)
    facts = _attachments(ctx, attachments)
    result = validate_build(ctx.base_facts(base), specs, facts, set(overrides), available_object_ids=set(ctx.effective))
    return specs, facts, result


def _save(campaign, build, ctx, base, facts, result, overrides) -> None:
    CampaignBuildSlot.query.filter_by(build_id=build.id).delete()
    db.session.flush()
    overridden = {issue.slot for issue in result.overridden}
    for key in sorted(facts):
        db.session.add(
            CampaignBuildSlot(
                campaign_id=campaign.id,
                build_id=build.id,
                slot_type=key[0],
                slot_index=key[1],
                attached_object_id=facts[key].object_id,
                is_override=key in overridden,
            )
        )


def create_build(
    campaign: Campaign,
    build_type: str,
    *,
    base_object_id: int,
    name: str,
    purpose: str | None = None,
    notes: str | None = None,
    attachments: dict[SlotKey, int] | None = None,
    overrides: set[SlotKey] | None = None,
) -> CampaignBuild:
    ctx = BuildContext(campaign)
    base = _require_base(ctx, build_type, base_object_id)
    clean = _clean_name(campaign, build_type, name)
    _specs, facts, result = _validate(ctx, build_type, base, attachments or {}, overrides or set())
    if not result.is_valid:
        raise BuildValidationError([i.message for i in result.errors])
    build = CampaignBuild(
        campaign_id=campaign.id,
        build_type=build_type,
        name=clean,
        purpose=(purpose or "").strip() or None,
        notes=(notes or "").strip() or None,
        base_object_id=base,
    )
    db.session.add(build)
    db.session.flush()
    _save(campaign, build, ctx, base, facts, result, overrides or set())
    db.session.commit()
    return build


def update_build(
    campaign: Campaign,
    build: CampaignBuild,
    *,
    base_object_id: int,
    name: str,
    purpose: str | None = None,
    notes: str | None = None,
    attachments: dict[SlotKey, int] | None = None,
    overrides: set[SlotKey] | None = None,
) -> CampaignBuild:
    _own(campaign, build)
    ctx = BuildContext(campaign)
    base = _require_base(ctx, build.build_type, base_object_id)
    clean = _clean_name(campaign, build.build_type, name, exclude_id=build.id)
    _specs, facts, result = _validate(ctx, build.build_type, base, attachments or {}, overrides or set())
    if not result.is_valid:
        raise BuildValidationError([i.message for i in result.errors])
    build.name, build.base_object_id = clean, base
    build.purpose = (purpose or "").strip() or None
    build.notes = (notes or "").strip() or None
    _save(campaign, build, ctx, base, facts, result, overrides or set())
    db.session.commit()
    return build


@dataclass(frozen=True)
class FittedComponents:
    """Result of moving a build's components onto a different base object."""

    attachments: dict[SlotKey, int]
    dropped: tuple[str, ...]  # names of components that have no matching slot on the new base

    @property
    def overrides(self) -> set[SlotKey]:
        """Manual compatibility overrides are never carried across a base change:
        an override accepted for one base says nothing about another, so the
        player is asked again if the new base makes a component incompatible."""
        return set()


def fit_attachments_to_base(
    campaign: Campaign,
    build_type: str,
    base_object_id: int,
    attachments: dict[SlotKey, int],
    *,
    ctx: BuildContext | None = None,
) -> FittedComponents:
    """Keep each component whose slot (type and index) still exists on the new
    base and accepts that kind of component; report the rest by name. Nothing is
    judged for compatibility here -- a kept component that the new base makes
    incompatible is shown as such in the editor and rejected on save unless the
    player explicitly overrides it. Raises `BuildError` for an invalid base."""
    ctx = ctx or BuildContext(campaign)
    base = _require_base(ctx, build_type, base_object_id)
    slots = {slot.key: slot for slot in slot_specs(ctx, build_type, base)}
    kept: dict[SlotKey, int] = {}
    dropped: list[str] = []
    for key, object_id in sorted(attachments.items()):
        winner = ctx.winner_of(object_id)
        slot = slots.get(key)
        if slot is not None and winner is not None and ctx.type_slug(winner) in slot.accepts:
            kept[key] = winner
        else:
            dropped.append(ctx.effective[winner]["name"] if winner in ctx.effective else _stored_name(object_id))
    return FittedComponents(kept, tuple(dropped))


@dataclass(frozen=True)
class BuildPreview:
    validation: ValidationResult
    summary: BuildSummary
    base: ObjectRef
    components: tuple[ObjectRef, ...]
    availability: str
    missing: tuple[ObjectRef, ...]


def preview_build(
    campaign: Campaign,
    build_type: str,
    *,
    base_object_id: int,
    attachments: dict[SlotKey, int] | None = None,
    overrides: set[SlotKey] | None = None,
) -> BuildPreview:
    """Validate and summarise an *unsaved* build for the editor's live preview."""
    ctx = BuildContext(campaign)
    base = _require_base(ctx, build_type, base_object_id)
    _specs, facts, result = _validate(ctx, build_type, base, attachments or {}, overrides or set())
    refs = tuple(ctx.ref(facts[k].object_id) for k in sorted(facts))
    base_ref = ctx.ref(base)
    missing = tuple(r for r in (base_ref, *refs) if not r.satisfied and r.category != "resource")
    summary = summarize_build(base_ref.name, ctx.attrs(base), [(r.name, ctx.attrs(r.object_id)) for r in refs])
    return BuildPreview(result, summary, base_ref, refs, STATE_PLANNED if missing else STATE_AVAILABLE, missing)


def delete_build(campaign: Campaign, build: CampaignBuild, *, unassign: bool = False) -> None:
    """Delete a build. A build assigned to soldiers is refused unless the caller
    explicitly asks to unassign it first (never a silent cascade)."""
    _own(campaign, build)
    assigned = CampaignSoldierBuild.query.filter_by(campaign_id=campaign.id, build_id=build.id).all()
    if assigned and not unassign:
        names = [db.session.get(CampaignSoldier, a.soldier_id).name for a in assigned]
        raise BuildInUseError(names)
    for assignment in assigned:
        db.session.delete(assignment)
    db.session.flush()  # no ORM relationship links the two tables; order the deletes explicitly
    db.session.delete(build)
    db.session.commit()


def _own(campaign: Campaign, build: CampaignBuild) -> None:
    if build.campaign_id != campaign.id:
        raise BuildError("That build belongs to a different campaign.")


def get_build(campaign: Campaign, build_id: int) -> CampaignBuild | None:
    """Campaign-scoped lookup: another campaign's build id returns None."""
    return CampaignBuild.query.filter_by(id=build_id, campaign_id=campaign.id).first()


def list_builds(campaign: Campaign, build_type: str | None = None) -> list[CampaignBuild]:
    query = CampaignBuild.query.filter_by(campaign_id=campaign.id)
    if build_type:
        query = query.filter_by(build_type=build_type)
    return query.order_by(CampaignBuild.build_type.asc(), CampaignBuild.name.asc()).all()


# --------------------------------------------------------------------- view


def build_view(campaign: Campaign, build: CampaignBuild, *, ctx: BuildContext | None = None) -> BuildView:
    _own(campaign, build)
    ctx = ctx or BuildContext(campaign)
    spec = get_build_type(build.build_type)
    base = ctx.normalize(build.base_object_id)
    specs = slot_specs(ctx, build.build_type, base)
    spec_by_key = {s.key: s for s in specs}
    stored = {(s.slot_type, s.slot_index): s for s in build.slots}
    attachments = {key: ctx.normalize(row.attached_object_id) for key, row in stored.items()}
    overrides = {key for key, row in stored.items() if row.is_override}
    facts = {}
    for key, object_id in attachments.items():
        facts[key] = (
            ctx.component_facts(object_id)
            if object_id in ctx.effective
            else ComponentFacts(object_id, _stored_name(object_id), "")
        )
    base_facts = ctx.base_facts(base) if base in ctx.effective else BaseFacts(base)
    result = validate_build(base_facts, specs, facts, overrides, available_object_ids=set(ctx.effective))

    components: list[ComponentView] = []
    for key in sorted(stored):
        slot = spec_by_key.get(key) or SlotSpec(key[0], key[1], f"{key[0].replace('_', ' ').title()} {key[1] + 1}", ())
        ref = ctx.ref(attachments[key]) or _missing_ref(attachments[key])
        components.append(ComponentView(slot, ref, check_compatibility(base_facts, facts[key]), key in overrides))
    empty = tuple(s for s in specs if s.key not in stored)

    base_ref = ctx.ref(base) or _missing_ref(base)
    needed = [base_ref, *(c.ref for c in components)]
    missing = tuple(r for r in needed if not r.satisfied and r.category != "resource")
    attrs_of = lambda oid: ctx.attrs(oid)
    summary = summarize_build(
        base_ref.name,
        attrs_of(base),
        [(c.ref.name, attrs_of(c.ref.object_id)) for c in components],
    )
    assigned = tuple(
        (a.soldier_id, db.session.get(CampaignSoldier, a.soldier_id).name)
        for a in CampaignSoldierBuild.query.filter_by(campaign_id=campaign.id, build_id=build.id).all()
    )
    return BuildView(
        id=build.id, name=build.name, build_type=build.build_type, type_label=spec.label,
        purpose=build.purpose, notes=build.notes, base=base_ref, components=tuple(components),
        empty_slots=empty, validation=result,
        availability=STATE_PLANNED if missing else STATE_AVAILABLE, missing=missing,
        summary=summary, assigned=assigned,
    )


def _stored_name(object_id: int) -> str:
    from app.models import CatalogObject

    obj = db.session.get(CatalogObject, object_id)
    return obj.name if obj else f"Object {object_id}"


def _missing_ref(object_id: int) -> ObjectRef:
    name = _stored_name(object_id)
    return ObjectRef(
        object_id=object_id, name=f"{name} (not in this campaign's sources)", type_slug="", type_name="Unavailable",
        category="unlockable", status="blocked", status_label="Unavailable", satisfied=False,
        requirements_met=False, primary_asset_id=None, glyph="",
    )


def list_build_views(campaign: Campaign, build_type: str | None = None) -> list[BuildView]:
    ctx = BuildContext(campaign)
    return [build_view(campaign, b, ctx=ctx) for b in list_builds(campaign, build_type)]
