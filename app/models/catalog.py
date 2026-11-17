"""Catalog tables: content-agnostic, never reference campaigns (DATABASE_SCHEMA.md §1).

Per ADR-0002/ADR-0010, nothing in this module may import or reference
`app.models.campaign` — enforced structurally (no such import exists) and
checked by `tests/test_schema_lint.py`.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CreatedAtMixin, Model, TimestampMixin

CONTENT_SOURCE_KINDS = ("base", "dlc", "total_conversion", "workshop_mod", "user")
CONTENT_SOURCE_RELATIONS = ("requires", "conflicts", "extends")
ATTRIBUTE_DATA_TYPES = ("string", "integer", "number", "boolean", "enum", "text")
ATTRIBUTE_DISPLAY_HINTS = ("badge", "stat_block", "paragraph", "hidden")


class ContentSource(Model, TimestampMixin):
    __tablename__ = "content_sources"
    __table_args__ = (
        CheckConstraint(f"kind IN {CONTENT_SOURCE_KINDS}", name="ck_content_sources_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False, default="0.0.0")
    steam_workshop_id: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    license: Mapped[str | None] = mapped_column(String, nullable=True)
    attribution_text: Mapped[str | None] = mapped_column(String, nullable=True)
    homepage_url: Mapped[str | None] = mapped_column(String, nullable=True)
    is_enabled_by_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)
    import_checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    imported_at: Mapped[str | None] = mapped_column(String, nullable=True)


class ContentSourceRelation(Model):
    __tablename__ = "content_source_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "related_source_id", "relation", name="uq_content_source_relation"
        ),
        CheckConstraint(
            f"relation IN {CONTENT_SOURCE_RELATIONS}", name="ck_content_source_relations_kind"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("content_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    related_source_id: Mapped[int] = mapped_column(
        ForeignKey("content_sources.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    relation: Mapped[str] = mapped_column(String, nullable=False)


class CatalogObjectType(Model):
    __tablename__ = "catalog_object_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    default_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_sources.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class CatalogAttributeDefinition(Model):
    __tablename__ = "catalog_attribute_definitions"
    __table_args__ = (
        UniqueConstraint("object_type_id", "key", name="uq_attribute_definitions_type_key"),
        CheckConstraint(
            f"data_type IN {ATTRIBUTE_DATA_TYPES}", name="ck_attribute_definitions_data_type"
        ),
        CheckConstraint(
            f"display_hint IS NULL OR display_hint IN {ATTRIBUTE_DISPLAY_HINTS}",
            name="ck_attribute_definitions_display_hint",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    object_type_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_object_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String, nullable=False)
    data_type: Mapped[str] = mapped_column(String, nullable=False)
    enum_values: Mapped[str | None] = mapped_column(String, nullable=True)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    is_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)
    default_value: Mapped[str | None] = mapped_column(String, nullable=True)
    display_hint: Mapped[str | None] = mapped_column(String, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_sources.id", ondelete="CASCADE"), nullable=True
    )


class CatalogObject(Model, TimestampMixin):
    __tablename__ = "catalog_objects"
    __table_args__ = (
        UniqueConstraint("source_id", "template_key", name="uq_catalog_objects_source_template"),
        UniqueConstraint("source_id", "slug", name="uq_catalog_objects_source_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("content_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_object_types.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    template_key: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    short_description: Mapped[str | None] = mapped_column(String, nullable=True)
    long_description: Mapped[str | None] = mapped_column(String, nullable=True)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attributes: Mapped[str] = mapped_column(String, nullable=False, default="{}")
    primary_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    overrides_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    replaces_object_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    patch: Mapped[str | None] = mapped_column(String, nullable=True)
    is_retired: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)
    is_orphaned_upstream: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)
    is_user_authored: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)


class CatalogObjectTag(Model):
    __tablename__ = "catalog_object_tags"

    object_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String, primary_key=True, index=True)


class CatalogRelationshipType(Model):
    __tablename__ = "catalog_relationship_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    inverse_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_relationship_types.id", ondelete="SET NULL"), nullable=True
    )
    is_quantified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_sources.id", ondelete="CASCADE"), nullable=True
    )


class CatalogRelationship(Model, CreatedAtMixin):
    __tablename__ = "catalog_relationships"
    __table_args__ = (
        UniqueConstraint(
            "source_pack_id",
            "relationship_type_id",
            "source_object_id",
            "target_object_id",
            "group_key",
            name="uq_catalog_relationships_natural_key",
        ),
        CheckConstraint(
            "group_logic IS NULL OR group_logic IN ('AND', 'OR')",
            name="ck_catalog_relationships_group_logic",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    relationship_type_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_relationship_types.id", ondelete="RESTRICT"), nullable=False
    )
    source_object_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    target_object_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_objects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    group_key: Mapped[str | None] = mapped_column(String, nullable=True)
    group_logic: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attributes: Mapped[str] = mapped_column(String, nullable=False, default="{}")
    source_pack_id: Mapped[int] = mapped_column(
        ForeignKey("content_sources.id", ondelete="CASCADE"), nullable=False
    )
    is_user_authored: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=0)
