"""Campaign soldier roster, stats and build assignment (ADR-0022).

Classes and ranks come from the campaign's *effective catalog* (so an LWotC or
modded campaign sees its own), default stats come from the catalog's stat
profiles for the campaign difficulty, and nothing here names a class, rank or
stat value. Campaign isolation (ADR-0010): every function takes the campaign
and touches only that campaign's rows.

Stats: `default_value` is captured once at creation and never recalculated;
`actual_value` is what the user's real unit has and is never overwritten by
defaults. Missing catalog data stays blank -- the app never invents a stat.
"""
from __future__ import annotations

from dataclasses import dataclass

from flask import current_app

from app.assets.storage import AssetValidationError, validate_and_reencode_image, write_to_disk
from app.extensions import db
from app.models import (
    Campaign,
    CampaignBuild,
    CampaignSoldier,
    CampaignSoldierBuild,
    CampaignSoldierStat,
)
from app.models.campaign import SOLDIER_STATUSES
from app.models.catalog import SOLDIER_STAT_KEYS
from app.repositories import assets as assets_repo
from app.repositories import soldier_stat_profiles as stat_profiles_repo
from app.services import builds as builds_service
from app.services.builds import BuildContext, BuildView
from app.services.dependency_index import ObjectRef

CLASS_TYPE_SLUG = "soldier_class"
RANK_TYPE_SLUG = "soldier_rank"

STAT_LABELS = {
    "health": "Health", "aim": "Aim", "defense": "Defense", "dodge": "Dodge", "mobility": "Mobility",
    "hacking": "Hacking", "will": "Will", "psi_offense": "Psi Offense", "armor": "Armor",
    "combat_intelligence": "Combat Intelligence",
}  # fmt: skip
KEY_STATS = ("health", "aim", "will", "mobility")

STAT_STATE_BLANK = "blank"
STAT_STATE_DEFAULT = "default"
STAT_STATE_CONFIRMED = "confirmed"
STAT_STATE_CUSTOM = "custom"


class SoldierError(ValueError):
    """Raised for invalid soldier operations."""


@dataclass(frozen=True)
class StatRow:
    key: str
    label: str
    actual: float | None
    default: float | None
    state: str

    @property
    def difference(self) -> float | None:
        if self.actual is None or self.default is None:
            return None
        return self.actual - self.default


@dataclass(frozen=True)
class AssignmentView:
    build_type: str
    assignment_slot: str
    type_label: str
    build: BuildView | None


@dataclass(frozen=True)
class SoldierView:
    id: int
    campaign_id: int
    name: str
    surname: str | None
    callsign: str | None
    status: str
    notes: str | None
    portrait_asset_id: int | None
    class_ref: ObjectRef | None
    rank_ref: ObjectRef | None
    stats: tuple[StatRow, ...]
    assignments: tuple[AssignmentView, ...]
    has_unconfirmed_defaults: bool = False
    legacy_rank: str | None = None

    @property
    def display_name(self) -> str:
        full = " ".join(p for p in (self.name, self.surname) if p)
        return f'{self.name} "{self.callsign}" {self.surname}'.replace("  ", " ").strip() if self.callsign and self.surname else (
            f'{self.name} "{self.callsign}"' if self.callsign else full
        )

    @property
    def key_stats(self) -> tuple[StatRow, ...]:
        by_key = {s.key: s for s in self.stats}
        return tuple(by_key[k] for k in KEY_STATS if k in by_key and by_key[k].actual is not None)

    def assignment(self, build_type: str, slot: str = "primary") -> BuildView | None:
        return next((a.build for a in self.assignments if a.build_type == build_type and a.assignment_slot == slot), None)


# ------------------------------------------------------------------ choices


def list_class_options(campaign: Campaign, *, ctx: BuildContext | None = None) -> list[ObjectRef]:
    ctx = ctx or BuildContext(campaign)
    return [ctx.ref(e["id"]) for e in ctx.effective_of_type_slugs((CLASS_TYPE_SLUG,))]


def list_rank_options(campaign: Campaign, *, ctx: BuildContext | None = None) -> list[ObjectRef]:
    ctx = ctx or BuildContext(campaign)
    entries = ctx.effective_of_type_slugs((RANK_TYPE_SLUG,))
    entries.sort(key=lambda e: (e.get("tier") or 0, e["name"].lower()))
    return [ctx.ref(e["id"]) for e in entries]


def _class_winner(ctx: BuildContext, class_object_id: int | None) -> int | None:
    if class_object_id is None:
        return None
    winner = ctx.winner_of(class_object_id)
    if winner is None or ctx.type_slug(winner) != CLASS_TYPE_SLUG:
        raise SoldierError("Choose a class that is available to this campaign's content sources.")
    return winner


def _rank_winner(ctx: BuildContext, rank_object_id: int | None) -> int | None:
    if rank_object_id is None:
        return None
    winner = ctx.winner_of(rank_object_id)
    if winner is None or ctx.type_slug(winner) != RANK_TYPE_SLUG:
        raise SoldierError("Choose a rank that is available to this campaign's content sources.")
    return winner


# ------------------------------------------------------------ default stats


def default_stats_for_class(campaign: Campaign, class_object_id: int | None, *, ctx: BuildContext | None = None) -> dict[str, float]:
    """Catalog default stats for this class at the campaign's difficulty.

    An override inherits its base object's profile; a `replaces` object does not
    (a different ruleset's class has different numbers, and absent data stays
    absent). Keys the catalog does not state are simply not returned."""
    if class_object_id is None:
        return {}
    ctx = ctx or BuildContext(campaign)
    winner = ctx.normalize(class_object_id)
    for object_id in ctx._chain(winner, follow_replaces=False):
        rows = stat_profiles_repo.get_for_object_and_difficulty(object_id, campaign.difficulty)
        usable = {r.stat_key: float(r.stat_default) for r in rows if r.stat_default is not None}
        if usable:
            return usable
    return {}


def _store_portrait(raw_bytes: bytes):
    max_mb = current_app.config.get("MAX_UPLOAD_MB", 8)
    try:
        stored = validate_and_reencode_image(raw_bytes, max_upload_mb=max_mb)
    except AssetValidationError as exc:
        raise SoldierError(str(exc)) from exc
    existing = assets_repo.get_by_content_hash(stored.content_hash)
    if existing is not None:
        return existing
    write_to_disk(current_app.config["ASSETS_PATH"], stored)
    return assets_repo.create_asset(
        storage_path=stored.relative_path, content_hash=stored.content_hash, mime_type=stored.mime_type,
        width=stored.width, height=stored.height, origin="upload", redistribution_allowed=False,
    )


# ---------------------------------------------------------------- operations


def _text(value: str | None, *, limit: int, label: str, required: bool = False) -> str | None:
    value = (value or "").strip()
    if required and not value:
        raise SoldierError(f"{label} is required.")
    if len(value) > limit:
        raise SoldierError(f"{label} must be {limit} characters or fewer.")
    return value or None


def create_soldier(
    campaign: Campaign,
    *,
    name: str,
    surname: str | None = None,
    callsign: str | None = None,
    class_object_id: int | None = None,
    rank_object_id: int | None = None,
    notes: str | None = None,
    status: str = "active",
    portrait_bytes: bytes | None = None,
) -> CampaignSoldier:
    ctx = BuildContext(campaign)
    if status not in SOLDIER_STATUSES:
        raise SoldierError("Unknown soldier status.")
    class_id = _class_winner(ctx, class_object_id)
    soldier = CampaignSoldier(
        campaign_id=campaign.id,
        name=_text(name, limit=80, label="A name", required=True),
        surname=_text(surname, limit=80, label="Surname"),
        callsign=_text(callsign, limit=40, label="Callsign"),
        class_object_id=class_id,
        rank_object_id=_rank_winner(ctx, rank_object_id),
        status=status,
        notes=_text(notes, limit=2000, label="Notes"),
    )
    if portrait_bytes:
        soldier.portrait_asset_id = _store_portrait(portrait_bytes).id
    db.session.add(soldier)
    db.session.flush()
    for key, value in default_stats_for_class(campaign, class_id, ctx=ctx).items():
        if key in SOLDIER_STAT_KEYS:
            db.session.add(
                CampaignSoldierStat(
                    campaign_id=campaign.id, soldier_id=soldier.id, stat_key=key,
                    actual_value=value, default_value=value, is_confirmed=False,
                )
            )
    db.session.commit()
    return soldier


def get_soldier(campaign: Campaign, soldier_id: int) -> CampaignSoldier | None:
    """Campaign-scoped lookup: another campaign's soldier id returns None."""
    return CampaignSoldier.query.filter_by(id=soldier_id, campaign_id=campaign.id).first()


def update_soldier(
    campaign: Campaign,
    soldier: CampaignSoldier,
    *,
    name: str,
    surname: str | None = None,
    callsign: str | None = None,
    class_object_id: int | None = None,
    rank_object_id: int | None = None,
    notes: str | None = None,
    status: str | None = None,
    portrait_bytes: bytes | None = None,
) -> CampaignSoldier:
    """Edit identity fields. Changing the class never recomputes stats: the
    values you entered stay yours (use `set_stats` to change them)."""
    _own(campaign, soldier)
    ctx = BuildContext(campaign)
    if status is not None and status not in SOLDIER_STATUSES:
        raise SoldierError("Unknown soldier status.")
    soldier.name = _text(name, limit=80, label="A name", required=True)
    soldier.surname = _text(surname, limit=80, label="Surname")
    soldier.callsign = _text(callsign, limit=40, label="Callsign")
    soldier.class_object_id = _class_winner(ctx, class_object_id)
    soldier.rank_object_id = _rank_winner(ctx, rank_object_id)
    soldier.notes = _text(notes, limit=2000, label="Notes")
    if status is not None:
        soldier.status = status
    if portrait_bytes:
        soldier.portrait_asset_id = _store_portrait(portrait_bytes).id
    db.session.commit()
    return soldier


def delete_soldier(campaign: Campaign, soldier: CampaignSoldier) -> None:
    _own(campaign, soldier)
    db.session.delete(soldier)
    db.session.commit()


def _own(campaign: Campaign, soldier: CampaignSoldier) -> None:
    if soldier.campaign_id != campaign.id:
        raise SoldierError("That soldier belongs to a different campaign.")


def _parse_stat(key: str, raw) -> float | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise SoldierError(f"{STAT_LABELS.get(key, key)} must be a number.") from None
    if value < 0 or value > 9999:
        raise SoldierError(f"{STAT_LABELS.get(key, key)} must be between 0 and 9999.")
    return value


def set_stats(campaign: Campaign, soldier: CampaignSoldier, values: dict[str, object]) -> None:
    """Record the soldier's *actual* stats (blank clears a value). Every key
    submitted becomes user-confirmed. Catalog defaults are never touched."""
    _own(campaign, soldier)
    parsed: dict[str, float | None] = {}
    for key, raw in values.items():
        if key not in SOLDIER_STAT_KEYS:
            raise SoldierError(f"'{key}' is not a recognised stat.")
        parsed[key] = _parse_stat(key, raw)
    rows = {r.stat_key: r for r in CampaignSoldierStat.query.filter_by(soldier_id=soldier.id, campaign_id=campaign.id)}
    for key, value in parsed.items():
        row = rows.get(key)
        if row is None:
            if value is None:
                continue
            row = CampaignSoldierStat(campaign_id=campaign.id, soldier_id=soldier.id, stat_key=key, default_value=None)
            db.session.add(row)
        row.actual_value = value
        row.is_confirmed = True
    db.session.commit()


def _stat_rows(soldier: CampaignSoldier) -> tuple[StatRow, ...]:
    by_key = {r.stat_key: r for r in soldier.stats}
    rows = []
    for key in SOLDIER_STAT_KEYS:
        row = by_key.get(key)
        if row is None or (row.actual_value is None and row.default_value is None):
            rows.append(StatRow(key, STAT_LABELS[key], None, None, STAT_STATE_BLANK))
            continue
        if not row.is_confirmed:
            state = STAT_STATE_DEFAULT if row.actual_value is not None else STAT_STATE_BLANK
        elif row.default_value is not None and row.actual_value == row.default_value:
            state = STAT_STATE_CONFIRMED
        else:
            state = STAT_STATE_CUSTOM
        rows.append(StatRow(key, STAT_LABELS[key], row.actual_value, row.default_value, state))
    return tuple(rows)


# ---------------------------------------------------------------- builds


def assign_build(
    campaign: Campaign, soldier: CampaignSoldier, build: CampaignBuild, *, assignment_slot: str = "primary"
) -> None:
    """Assign a saved build (shared, not copied). Re-assigning a slot replaces
    the previous assignment. Unavailable/unlocked state never blocks this."""
    _own(campaign, soldier)
    if build.campaign_id != campaign.id:
        raise SoldierError("That build belongs to a different campaign.")
    spec = builds_service.get_build_type(build.build_type)
    if assignment_slot not in spec.assignment_slots:
        raise SoldierError(f"{spec.label} cannot be assigned to '{assignment_slot}'.")
    row = CampaignSoldierBuild.query.filter_by(
        soldier_id=soldier.id, build_type=build.build_type, assignment_slot=assignment_slot
    ).first()
    if row is None:
        row = CampaignSoldierBuild(
            campaign_id=campaign.id, soldier_id=soldier.id, build_type=build.build_type, assignment_slot=assignment_slot
        )
        db.session.add(row)
    row.build_id = build.id
    db.session.commit()


def unassign_build(campaign: Campaign, soldier: CampaignSoldier, build_type: str, assignment_slot: str = "primary") -> None:
    _own(campaign, soldier)
    CampaignSoldierBuild.query.filter_by(
        soldier_id=soldier.id, campaign_id=campaign.id, build_type=build_type, assignment_slot=assignment_slot
    ).delete()
    db.session.commit()


# ------------------------------------------------------------------- views


def soldier_view(campaign: Campaign, soldier: CampaignSoldier, *, ctx: BuildContext | None = None) -> SoldierView:
    _own(campaign, soldier)
    ctx = ctx or BuildContext(campaign)
    stats = _stat_rows(soldier)
    assignments = []
    existing = {(a.build_type, a.assignment_slot): a for a in soldier.build_assignments}
    for build_type, spec in builds_service.BUILD_TYPES.items():
        for slot in spec.assignment_slots:
            row = existing.get((build_type, slot))
            build = db.session.get(CampaignBuild, row.build_id) if row else None
            assignments.append(
                AssignmentView(build_type, slot, spec.label, builds_service.build_view(campaign, build, ctx=ctx) if build else None)
            )
    return SoldierView(
        id=soldier.id, campaign_id=soldier.campaign_id, name=soldier.name, surname=soldier.surname,
        callsign=soldier.callsign, status=soldier.status, notes=soldier.notes,
        portrait_asset_id=soldier.portrait_asset_id,
        class_ref=ctx.ref(soldier.class_object_id) if soldier.class_object_id else None,
        rank_ref=ctx.ref(soldier.rank_object_id) if soldier.rank_object_id else None,
        stats=stats, assignments=tuple(assignments),
        has_unconfirmed_defaults=any(s.state == STAT_STATE_DEFAULT for s in stats),
        legacy_rank=soldier.rank,
    )


def list_soldier_views(campaign: Campaign) -> list[SoldierView]:
    ctx = BuildContext(campaign)
    soldiers = CampaignSoldier.query.filter_by(campaign_id=campaign.id).order_by(CampaignSoldier.name.asc(), CampaignSoldier.id.asc()).all()
    return [soldier_view(campaign, s, ctx=ctx) for s in soldiers]


__all__ = [
    "SoldierError", "SoldierView", "StatRow", "assign_build", "create_soldier", "default_stats_for_class",
    "delete_soldier", "get_soldier", "list_class_options", "list_rank_options", "list_soldier_views",
    "set_stats", "soldier_view", "unassign_build", "update_soldier",
]  # fmt: skip
