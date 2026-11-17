"""Asset upload/attach orchestration (FR-AST-1/2/3/5/6, ARCHITECTURE.md §8).

Wires `app/assets/storage.py` (pure file validation/IO) to the `assets`/
`object_assets` repository rows and to `current_app.config` for the
assets-root path and upload-size limit.
"""
from __future__ import annotations

from flask import current_app

from app.assets.storage import (
    AssetValidationError,
    validate_and_reencode_image,
    write_to_disk,
)
from app.models import Asset, CatalogObjectType
from app.repositories import assets as repo


def upload_image_for_object(
    object_id: int,
    *,
    role: str,
    raw_bytes: bytes,
    source_url: str | None = None,
    license: str | None = None,
    attribution_text: str | None = None,
) -> Asset:
    """Validate, store, and attach an uploaded image to a catalog object.

    Re-encoding happens before anything touches the DB or disk, so a
    rejected upload leaves no trace (FR-AST-2, ARCHITECTURE.md §9 upload
    validation).
    """
    max_mb = current_app.config.get("MAX_UPLOAD_MB", 8)
    stored = validate_and_reencode_image(raw_bytes, max_upload_mb=max_mb)

    existing = repo.get_by_content_hash(stored.content_hash)
    if existing is not None:
        asset = existing
    else:
        write_to_disk(current_app.config["ASSETS_PATH"], stored)
        asset = repo.create_asset(
            storage_path=stored.relative_path,
            content_hash=stored.content_hash,
            mime_type=stored.mime_type,
            width=stored.width,
            height=stored.height,
            origin="upload",
            source_url=(source_url or "").strip() or None,
            license=(license or "").strip() or None,
            attribution_text=(attribution_text or "").strip() or None,
            redistribution_allowed=False,
        )

    repo.attach_to_object(object_id, asset.id, role)
    if role == "icon" or role == "portrait":
        from app.repositories.catalog_objects import get_object, update_object

        obj = get_object(object_id)
        if obj is not None and obj.primary_asset_id is None:
            update_object(obj, primary_asset_id=asset.id)

    return asset


def read_asset_bytes(asset: Asset) -> bytes:
    from app.assets.storage import read_from_disk

    return read_from_disk(current_app.config["ASSETS_PATH"], asset.storage_path)


# Inline SVG fallback glyphs per object-type category (ADR-0006, FR-AST-5):
# authored in-house, not extracted from the game (ADR-0008). Generic
# geometric shapes, not tied to any specific XCOM object's name/appearance.
_DEFAULT_GLYPH = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
    '<rect x="4" y="4" width="16" height="16" rx="1"/>'
    '<path d="M4 15l4-4 4 4 4-6 4 4"/>'
    "</svg>"
)


def fallback_glyph_for_type(object_type: CatalogObjectType | None) -> str:
    """Return an inline SVG glyph markup for objects without an image.

    Every `catalog_object_type` can declare its own `default_asset_id` in
    the future; until Phase 3's importer seeds those, every type shares
    this one generic glyph -- still satisfies FR-AST-5 ("every object
    resolves to *some* image") without inventing per-type iconography here.
    """
    del object_type  # reserved for future per-type glyphs via default_asset_id
    return _DEFAULT_GLYPH


__all__ = [
    "AssetValidationError",
    "fallback_glyph_for_type",
    "read_asset_bytes",
    "upload_image_for_object",
]
