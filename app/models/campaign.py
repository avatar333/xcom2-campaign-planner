"""Campaign-scoped tables (DATABASE_SCHEMA.md §3).

Per ADR-0010/ADR-0002: every table here carries `campaign_id` with
`ON DELETE CASCADE`, and nothing under `app.models.catalog`/`app.models.assets`
ever references these tables. Repository functions over these models must
take `campaign_id` as a mandatory first argument and filter by it --
see `app/repositories/campaigns.py` and `tests/test_campaign_isolation.py`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import CreatedAtMixin, Model, TimestampMixin

if TYPE_CHECKING:
    from app.models.catalog import ContentSource

CAMPAIGN_DIFFICULTIES = ("rookie", "veteran", "commander", "legend")
CAMPAIGN_STATUSES = ("active", "archived")
OBJECT_STATE_STATUSES = ("unknown", "locked", "available", "in_progress", "completed", "owned")
PLAN_ITEM_STATUSES = ("blocked", "ready", "done", "unknown")
AVENGER_BUILD_STATES = ("none", "planned", "constructing", "built")
SOLDIER_STATUSES = ("active", "wounded", "dead", "retired")


class Campaign(Model, TimestampMixin):
    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint(f"difficulty IN {CAMPAIGN_DIFFICULTIES}", name="ck_campaigns_difficulty"),
        CheckConstraint(f"status IN {CAMPAIGN_STATUSES}", name="ck_campaigns_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    game_profile_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    difficulty: Mapped[str] = mapped_column(String, nullable=False, default="commander")
    is_ironman: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    effective_catalog_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    content_sources: Mapped[list[CampaignContentSource]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    avenger_cells: Mapped[list[CampaignAvengerCell]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    object_states: Mapped[list[CampaignObjectState]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    events: Mapped[list[CampaignEvent]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    plans: Mapped[list[CampaignPlan]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    soldiers: Mapped[list[CampaignSoldier]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class CampaignContentSource(Model, CreatedAtMixin):
    __tablename__ = "campaign_content_sources"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "content_source_id", name="uq_campaign_content_sources"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_source_id: Mapped[int] = mapped_column(
        ForeignKey("content_sources.id", ondelete="RESTRICT"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=1)
    precedence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    campaign: Mapped[Campaign] = relationship(back_populates="content_sources")
    content_source: Mapped[ContentSource] = relationship()


class CampaignAvengerCell(Model, TimestampMixin):
    """One of the 12 cells of a campaign's own Avenger construction grid
    (ADR-0020). Three independent concepts live in three columns -- never
    merged into one "facility name" field:

    1. room / excavation state  -> `room_state_object_id` (NULL = not yet
       configured by the player),
    2. facility assignment      -> `facility_object_id`,
    3. facility build state     -> `build_state` (lifecycle of the assignment).

    Position is the stable `coordinate` (plus `grid_row`/`grid_column`), not an
    ordering. All catalog references are RESTRICT: catalog data stays global
    and is never rewritten by campaign planning (ADR-0002/0010).
    """

    __tablename__ = "campaign_avenger_cells"
    __table_args__ = (
        UniqueConstraint("campaign_id", "coordinate", name="uq_campaign_avenger_cells_coordinate"),
        UniqueConstraint(
            "campaign_id", "grid_row", "grid_column", name="uq_campaign_avenger_cells_position"
        ),
        CheckConstraint("grid_row BETWEEN 1 AND 4", name="ck_campaign_avenger_cells_row"),
        CheckConstraint("grid_column BETWEEN 1 AND 3", name="ck_campaign_avenger_cells_column"),
        CheckConstraint(
            "coordinate = 'R' || grid_row || 'C' || grid_column",
            name="ck_campaign_avenger_cells_coordinate",
        ),
        CheckConstraint(
            f"build_state IN {AVENGER_BUILD_STATES}", name="ck_campaign_avenger_cells_build_state"
        ),
        CheckConstraint(
            "(facility_object_id IS NULL AND build_state = 'none') "
            "OR (facility_object_id IS NOT NULL AND build_state <> 'none')",
            name="ck_campaign_avenger_cells_facility_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    coordinate: Mapped[str] = mapped_column(String, nullable=False)
    grid_row: Mapped[int] = mapped_column(Integer, nullable=False)
    grid_column: Mapped[int] = mapped_column(Integer, nullable=False)
    room_state_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    facility_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    build_state: Mapped[str] = mapped_column(String, nullable=False, default="none")
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="avenger_cells")


class CampaignResearchQueueItem(Model, CreatedAtMixin):
    """One entry of a campaign's ordered research queue (ADR-0021).

    The queue holds *planned* and *in-progress* projects, in the order the
    player intends to research them. `position` is the sole ordering key and is
    renormalised to 0..n-1 by the service on every write. Whether an entry is
    planned or in progress lives in `campaign_object_state.status`; completion
    removes the entry. The catalog object is never touched (ADR-0002).
    """

    __tablename__ = "campaign_research_queue"
    __table_args__ = (
        UniqueConstraint("campaign_id", "catalog_object_id", name="uq_campaign_research_queue_object"),
        CheckConstraint("position >= 0", name="ck_campaign_research_queue_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    catalog_object_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CampaignObjectState(Model, TimestampMixin):
    __tablename__ = "campaign_object_state"
    __table_args__ = (
        UniqueConstraint("campaign_id", "catalog_object_id", name="uq_campaign_object_state"),
        CheckConstraint(f"status IN {OBJECT_STATE_STATUSES}", name="ck_campaign_object_state_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    catalog_object_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prerequisite_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    state_attributes: Mapped[str] = mapped_column(String, nullable=False, default="{}")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=1)
    # Phase 6 (ADR-0021): when/in what order this campaign completed the object.
    # `completion_sequence` is a per-campaign counter (1, 2, 3 ...) so the order
    # survives identical timestamps; both are NULL unless status is completed/owned.
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    completion_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="object_states")


class CampaignEvent(Model):
    __tablename__ = "campaign_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    catalog_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=False)
    in_game_label: Mapped[str | None] = mapped_column(String, nullable=True)
    occurred_at: Mapped[str] = mapped_column(String, nullable=False)

    campaign: Mapped[Campaign] = relationship(back_populates="events")


class CampaignPlan(Model, TimestampMixin):
    __tablename__ = "campaign_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="plans")
    items: Mapped[list[CampaignPlanItem]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class CampaignPlanItem(Model, TimestampMixin):
    __tablename__ = "campaign_plan_items"
    __table_args__ = (
        CheckConstraint(f"cached_status IN {PLAN_ITEM_STATUSES}", name="ck_plan_items_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("campaign_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    catalog_object_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    action_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rationale: Mapped[str | None] = mapped_column(String, nullable=True)
    depends_on_plan_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaign_plan_items.id", ondelete="SET NULL"), nullable=True
    )
    cached_status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")

    plan: Mapped[CampaignPlan] = relationship(back_populates="items")


class CampaignSoldier(Model, TimestampMixin):
    __tablename__ = "campaign_soldiers"
    __table_args__ = (
        CheckConstraint(f"status IN {SOLDIER_STATUSES}", name="ck_campaign_soldiers_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    class_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="SET NULL"), nullable=True
    )
    rank: Mapped[str | None] = mapped_column(String, nullable=True)
    # Phase 7 (ADR-0022)
    surname: Mapped[str | None] = mapped_column(String, nullable=True)
    callsign: Mapped[str | None] = mapped_column(String, nullable=True)
    rank_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    portrait_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="soldiers")
    loadouts: Mapped[list[CampaignSoldierLoadout]] = relationship(
        back_populates="soldier", cascade="all, delete-orphan"
    )
    stats: Mapped[list[CampaignSoldierStat]] = relationship(cascade="all, delete-orphan")
    build_assignments: Mapped[list[CampaignSoldierBuild]] = relationship(cascade="all, delete-orphan")


class CampaignSoldierStat(Model, TimestampMixin):
    """One tracked stat of one soldier (ADR-0022). `actual_value` is what the
    player's real unit has; `default_value` is the catalog default captured *at
    creation* and never recomputed, so manual values are never recalculated
    away and "actual vs class default" comparisons stay possible. NULL means
    unknown / not tracked -- the app never invents a value."""

    __tablename__ = "campaign_soldier_stats"
    __table_args__ = (
        UniqueConstraint("soldier_id", "stat_key", name="uq_campaign_soldier_stats"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    soldier_id: Mapped[int] = mapped_column(
        ForeignKey("campaign_soldiers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stat_key: Mapped[str] = mapped_column(String, nullable=False)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    default_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=0)


class CampaignBuild(Model, TimestampMixin):
    """A reusable, named equipment build (weapon, armour, or any future type)
    owned by one campaign (ADR-0022). `build_type` is an open string resolved
    through a registry in code, never a CHECK list, so a new kind of build needs
    no schema change. The build points at a catalog base object; its attached
    components live in `campaign_build_slots`."""

    __tablename__ = "campaign_builds"
    __table_args__ = (
        UniqueConstraint("campaign_id", "build_type", "name", name="uq_campaign_builds_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    build_type: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    base_object_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    slots: Mapped[list[CampaignBuildSlot]] = relationship(
        back_populates="build", cascade="all, delete-orphan", order_by="CampaignBuildSlot.slot_type, CampaignBuildSlot.slot_index"
    )


class CampaignBuildSlot(Model):
    """One filled, typed slot of a build: Build -> typed slot -> attached
    catalog object. `slot_type` is data (e.g. "weapon_upgrade", "utility", or a
    mod's own type), never a column per component kind. `is_override` records
    that the user deliberately attached something the catalog's compatibility
    data does not permit/confirm (ADR-0022)."""

    __tablename__ = "campaign_build_slots"
    __table_args__ = (
        UniqueConstraint("build_id", "slot_type", "slot_index", name="uq_campaign_build_slots"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    build_id: Mapped[int] = mapped_column(
        ForeignKey("campaign_builds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slot_type: Mapped[str] = mapped_column(String, nullable=False)
    slot_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attached_object_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    is_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=0)

    build: Mapped[CampaignBuild] = relationship(back_populates="slots")


class CampaignSoldierBuild(Model, TimestampMixin):
    """Assignment of a saved build to a soldier (ADR-0022). The soldier points
    at the *shared* build (no copy), so the build is reusable and edits are seen
    by every assigned soldier. `assignment_slot` distinguishes e.g. a primary
    from a future secondary weapon without a schema change."""

    __tablename__ = "campaign_soldier_builds"
    __table_args__ = (
        UniqueConstraint("soldier_id", "build_type", "assignment_slot", name="uq_campaign_soldier_builds"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    soldier_id: Mapped[int] = mapped_column(
        ForeignKey("campaign_soldiers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    build_id: Mapped[int] = mapped_column(
        ForeignKey("campaign_builds.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    build_type: Mapped[str] = mapped_column(String, nullable=False)
    assignment_slot: Mapped[str] = mapped_column(String, nullable=False, default="primary")


class CampaignSoldierLoadout(Model, CreatedAtMixin):
    __tablename__ = "campaign_soldier_loadouts"
    __table_args__ = (
        UniqueConstraint("soldier_id", "slot", "is_planned", name="uq_soldier_loadout_slot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    soldier_id: Mapped[int] = mapped_column(
        ForeignKey("campaign_soldiers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slot: Mapped[str] = mapped_column(String, nullable=False)
    item_object_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="RESTRICT"), nullable=False
    )
    is_planned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)

    soldier: Mapped[CampaignSoldier] = relationship(back_populates="loadouts")


class CampaignStrategyProfile(Model):
    __tablename__ = "campaign_strategy_profiles"

    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True
    )
    strategy_profile_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=1)
