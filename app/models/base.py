"""Shared model utilities: timestamp columns and naming helpers.

All timestamps are stored as ISO-8601 UTC text columns per
``DATABASE_SCHEMA.md`` ("All timestamps are TEXT ISO-8601 UTC").
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TimestampMixin:
    """created_at / updated_at TEXT ISO-8601 columns, per DATABASE_SCHEMA.md."""

    created_at: Mapped[str] = mapped_column(String, nullable=False, default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=utc_now_iso, onupdate=utc_now_iso
    )


class CreatedAtMixin:
    """created_at-only TEXT ISO-8601 column, for append-only / link tables."""

    created_at: Mapped[str] = mapped_column(String, nullable=False, default=utc_now_iso)


Model = db.Model
