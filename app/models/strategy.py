"""Strategy (opinion) tables, kept separate from catalog facts (ADR-0009)."""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CreatedAtMixin, Model


class StrategyProfile(Model, CreatedAtMixin):
    __tablename__ = "strategy_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    content_source_scope: Mapped[int | None] = mapped_column(
        ForeignKey("content_sources.id", ondelete="CASCADE"), nullable=True
    )


class StrategyRecommendation(Model):
    __tablename__ = "strategy_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_profile_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    recommended_action: Mapped[str] = mapped_column(String, nullable=False)
    rationale: Mapped[str | None] = mapped_column(String, nullable=True)
    priority_hint: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
