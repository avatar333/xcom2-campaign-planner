"""Campaign-scoped tables (DATABASE_SCHEMA.md §3).

Per ADR-0010/ADR-0002: every table here carries `campaign_id` with
`ON DELETE CASCADE`, and nothing under `app.models.catalog`/`app.models.assets`
ever references these tables. Repository functions over these models must
take `campaign_id` as a mandatory first argument and filter by it --
see `app/repositories/campaigns.py` and `tests/test_campaign_isolation.py`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
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
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    portrait_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="soldiers")
    loadouts: Mapped[list[CampaignSoldierLoadout]] = relationship(
        back_populates="soldier", cascade="all, delete-orphan"
    )


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
