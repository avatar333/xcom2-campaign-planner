"""Asset tables: `assets`, `object_assets` (DATABASE_SCHEMA.md §2)."""
from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CreatedAtMixin, Model

ASSET_ORIGINS = ("upload", "pack_bundled", "fetched_url", "generated_default")
OBJECT_ASSET_ROLES = ("icon", "portrait", "banner", "gallery")


class Asset(Model, CreatedAtMixin):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(f"origin IN {ASSET_ORIGINS}", name="ck_assets_origin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    storage_path: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    content_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    license: Mapped[str | None] = mapped_column(String, nullable=True)
    attribution_text: Mapped[str | None] = mapped_column(String, nullable=True)
    redistribution_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0
    )


class ObjectAsset(Model):
    __tablename__ = "object_assets"
    __table_args__ = (
        CheckConstraint(f"role IN {OBJECT_ASSET_ROLES}", name="ck_object_assets_role"),
    )

    object_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="CASCADE"), primary_key=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String, primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
