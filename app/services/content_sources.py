"""Content-source lifecycle service (FR-SRC-1/3/6, ADR-0005 slug conventions).

Kept as a service (not inlined in routes) for the same reason as campaign
lifecycle: slug derivation/uniqueness and the "retire, don't delete a
source in use" rule are business logic, not raw CRUD.
"""
from __future__ import annotations

import re

from app.models import ContentSource
from app.models.catalog import CONTENT_SOURCE_KINDS
from app.repositories import content_sources as repo

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class ContentSourceValidationError(ValueError):
    pass


def slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "source"


def _unique_slug(base_slug: str) -> str:
    slug = base_slug
    suffix = 2
    while repo.get_content_source_by_slug(slug) is not None:
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def _validate_fields(name: str, kind: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ContentSourceValidationError("Name is required.")
    if len(name) > 200:
        raise ContentSourceValidationError("Name must be 200 characters or fewer.")
    if kind not in CONTENT_SOURCE_KINDS:
        raise ContentSourceValidationError(f"Kind must be one of: {', '.join(CONTENT_SOURCE_KINDS)}.")
    return name


def create_content_source(
    *,
    name: str,
    kind: str,
    version: str = "0.0.0",
    steam_workshop_id: str | None = None,
    description: str | None = None,
    license: str | None = None,
    attribution_text: str | None = None,
    homepage_url: str | None = None,
    slug: str | None = None,
) -> ContentSource:
    name = _validate_fields(name, kind)
    chosen_slug = _unique_slug(slugify(slug or name))

    return repo.create_content_source(
        slug=chosen_slug,
        name=name,
        kind=kind,
        version=(version or "0.0.0").strip() or "0.0.0",
        steam_workshop_id=(steam_workshop_id or "").strip() or None,
        description=(description or "").strip() or None,
        license=(license or "").strip() or None,
        attribution_text=(attribution_text or "").strip() or None,
        homepage_url=(homepage_url or "").strip() or None,
        is_builtin=False,
        is_enabled_by_default=False,
    )


def update_content_source(
    source: ContentSource,
    *,
    name: str,
    kind: str,
    version: str = "0.0.0",
    steam_workshop_id: str | None = None,
    description: str | None = None,
    license: str | None = None,
    attribution_text: str | None = None,
    homepage_url: str | None = None,
) -> ContentSource:
    name = _validate_fields(name, kind)
    return repo.update_content_source(
        source,
        name=name,
        kind=kind,
        version=(version or "0.0.0").strip() or "0.0.0",
        steam_workshop_id=(steam_workshop_id or "").strip() or None,
        description=(description or "").strip() or None,
        license=(license or "").strip() or None,
        attribution_text=(attribution_text or "").strip() or None,
        homepage_url=(homepage_url or "").strip() or None,
    )


class ContentSourceInUseError(ValueError):
    """Raised when attempting to delete a source that owns objects or is
    attached to a campaign -- it must be disabled/retired, never deleted
    (DATABASE_SCHEMA.md §1.2)."""


def delete_content_source(source: ContentSource) -> None:
    if source.is_builtin:
        raise ContentSourceInUseError("Built-in sources cannot be deleted.")
    if repo.has_catalog_objects(source):
        raise ContentSourceInUseError(
            "This source has catalog objects and cannot be deleted. Remove its objects first."
        )
    if repo.is_used_by_any_campaign(source):
        raise ContentSourceInUseError(
            "This source is attached to at least one campaign and cannot be deleted."
        )
    repo.delete_content_source(source)


def get_or_create_user_source(name: str = "Custom / User Content") -> ContentSource:
    """Resolve the implicit user source used for manually-authored content
    (DOMAIN_MODEL.md §3). The seeded `user-local` source already covers the
    common case; this exists for symmetry/future multi-user-source support."""
    existing = repo.get_content_source_by_slug("user-local")
    if existing is not None:
        return existing
    return create_content_source(name=name, kind="user", slug="user-local")
