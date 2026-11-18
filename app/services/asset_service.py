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


MAX_FETCH_MB = 8
_FETCH_TIMEOUT_SECONDS = 10


def _assert_safe_fetch_url(url: str) -> None:
    """Minimal SSRF guard for the FR-AST-4 "fetch from URL" action: only
    `http(s)` to a public hostname/IP is allowed. This runs once up front
    (scheme + a DNS resolution check) before any network request is made;
    it is not a full SSRF-hardened proxy (no redirect-chain re-validation,
    no protection against DNS rebinding between this check and the actual
    request) -- acceptable for a single-user local tool, but not something
    to copy into a multi-tenant/public-facing service.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise AssetValidationError("Only http:// and https:// URLs can be fetched.")
    host = parsed.hostname
    if not host:
        raise AssetValidationError("The URL has no hostname to fetch from.")

    try:
        resolved = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise AssetValidationError(f"Could not resolve host '{host}': {exc}") from exc

    for family, _type, _proto, _canon, sockaddr in resolved:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise AssetValidationError(
                f"Refusing to fetch from '{host}': resolves to a private/internal address."
            )


def fetch_image_for_object(
    object_id: int,
    *,
    role: str,
    url: str,
    license: str | None = None,
    attribution_text: str | None = None,
) -> Asset:
    """FR-AST-4: user-initiated "fetch from URL" -- downloads the image
    bytes once, validates/re-encodes them exactly like a direct upload, and
    stores the result locally (`origin='fetched_url'`). After this call the
    runtime UI only ever serves the local copy; `url` is kept purely as
    provenance (`source_url`), never re-fetched at render time (NFR-4).
    """
    import urllib.error
    import urllib.request

    _assert_safe_fetch_url(url)

    max_mb = current_app.config.get("MAX_UPLOAD_MB", MAX_FETCH_MB)
    request = urllib.request.Request(url, headers={"User-Agent": "xcom2-campaign-planner/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT_SECONDS) as response:
            raw_bytes = response.read(max_mb * 1024 * 1024 + 1)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        raise AssetValidationError(f"Could not fetch image from URL: {exc}") from exc

    if len(raw_bytes) > max_mb * 1024 * 1024:
        raise AssetValidationError(f"Fetched file exceeds the {max_mb} MB limit.")

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
            origin="fetched_url",
            source_url=url.strip(),
            license=(license or "").strip() or None,
            attribution_text=(attribution_text or "").strip() or None,
            redistribution_allowed=False,
        )

    repo.attach_to_object(object_id, asset.id, role)
    if role in ("icon", "portrait"):
        from app.repositories.catalog_objects import get_object, update_object

        obj = get_object(object_id)
        if obj is not None and obj.primary_asset_id is None:
            update_object(obj, primary_asset_id=asset.id)

    return asset


def import_local_image_for_object(
    object_id: int,
    *,
    role: str,
    file_path: str,
    source_url: str | None = None,
    source_name: str | None = None,
    license: str | None = None,
    attribution_text: str | None = None,
    redistribution_allowed: bool = False,
    import_status: str = "imported",
) -> Asset:
    """Validate and store an image from a local filesystem path, attaching
    it to `object_id` (Phase 4: pack-bundled placeholder assets, `ADR-0008`,
    and the asset-importer seam, brief "Asset importer seam").

    Distinct from `upload_image_for_object` (web-upload bytes): this reads
    from a path on disk (a pack's own `assets/` directory, or a user's local
    import directory), never from an HTTP request.
    """
    from pathlib import Path

    raw_bytes = Path(file_path).read_bytes()
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
            origin="pack_bundled",
            source_url=(source_url or "").strip() or None,
            source_name=(source_name or "").strip() or None,
            original_filename=Path(file_path).name,
            license=(license or "").strip() or None,
            attribution_text=(attribution_text or "").strip() or None,
            redistribution_allowed=redistribution_allowed,
            import_status=import_status,
        )

    repo.attach_to_object(object_id, asset.id, role)
    if role in ("icon", "portrait"):
        from app.repositories.catalog_objects import get_object, update_object

        obj = get_object(object_id)
        if obj is not None and obj.primary_asset_id is None:
            update_object(obj, primary_asset_id=asset.id)

    return asset


def record_pending_asset_manifest_entry(
    object_id: int,
    *,
    role: str,
    source_url: str | None = None,
    source_name: str | None = None,
    original_filename: str | None = None,
    license: str | None = None,
    attribution_text: str | None = None,
    redistribution_allowed: bool = False,
) -> Asset:
    """Record provenance/intent for an image this app does not (yet) have
    bytes for -- e.g. a curated pack citing "this object's real icon comes
    from UFOpaedia, URL X, but we are not bundling that copyrighted image
    without confirmed redistribution rights" (`ADR-0008`).

    Creates a placeholder `assets` row with `import_status='pending'` and a
    deterministic, content-free `storage_path` (there are no bytes yet, so
    nothing is written to disk) so the manifest entry is inspectable from
    the database and the asset-importer seam (brief "Asset importer seam")
    has a concrete row to update once a legally-sourced local file is
    provided for this object/role.
    """
    import hashlib

    key = f"pending:{object_id}:{role}:{source_url or ''}".encode()
    synthetic_hash = hashlib.sha256(key).hexdigest()
    existing = repo.get_by_content_hash(synthetic_hash)
    if existing is not None:
        asset = existing
    else:
        asset = repo.create_asset(
            storage_path=f"pending/{synthetic_hash}.manifest",
            content_hash=synthetic_hash,
            mime_type="application/x-pending-manifest-entry",
            origin="generated_default",
            source_url=(source_url or "").strip() or None,
            source_name=(source_name or "").strip() or None,
            original_filename=(original_filename or "").strip() or None,
            license=(license or "").strip() or None,
            attribution_text=(attribution_text or "").strip() or None,
            redistribution_allowed=redistribution_allowed,
            import_status="pending",
        )
    repo.attach_to_object(object_id, asset.id, role)
    return asset


# Inline SVG fallback glyphs per object-type category (ADR-0006, FR-AST-5):
# authored in-house, not extracted from the game (ADR-0008). Generic
# geometric shapes, not tied to any specific XCOM object's name/appearance.
_DEFAULT_GLYPH = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
    '<rect x="4" y="4" width="16" height="16" rx="1"/>'
    '<path d="M4 15l4-4 4 4 4-6 4 4"/>'
    "</svg>"
)

# Per-type-slug glyphs (Phase 4 `FR-AST-5` upgrade): still generic geometric
# shapes authored in-house, just differentiated enough that a weapon card
# and a facility card read differently at a glance even with no uploaded
# image. Falls back to `_DEFAULT_GLYPH` for any slug not listed here, so a
# pack-introduced type (`ADR-0003`) never renders a missing/broken icon.
_GLYPHS_BY_TYPE_SLUG: dict[str, str] = {
    "weapon": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<path d="M3 13l8-8 2 2-1 1 2 2-1 1 2 2-1 1-2-2-1 1-8-8z"/>'
        '<path d="M15 5l4 4"/>'
        "</svg>"
    ),
    "weapon_upgrade": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<path d="M4 20l6-6M12 4l2 2-6 6-2-2z"/>'
        '<path d="M14 6l4 4-6 6-4-4z"/>'
        "</svg>"
    ),
    "armor": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<path d="M12 3l7 3v6c0 5-3 8-7 9-4-1-7-4-7-9V6z"/>'
        "</svg>"
    ),
    "utility_item": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<circle cx="12" cy="12" r="7"/><path d="M12 9v3l2 2"/>'
        "</svg>"
    ),
    "ammo": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<rect x="9" y="3" width="6" height="12" rx="2"/><path d="M12 15v6"/>'
        "</svg>"
    ),
    "grenade": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<circle cx="12" cy="14" r="7"/><path d="M12 7V4l2-1"/>'
        "</svg>"
    ),
    "resource": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<path d="M12 3l9 5v8l-9 5-9-5V8z"/><path d="M3 8l9 5 9-5M12 13v8"/>'
        "</svg>"
    ),
    "research_project": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<circle cx="10" cy="10" r="6"/><path d="M15 15l5 5"/>'
        "</svg>"
    ),
    "autopsy_project": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<circle cx="10" cy="10" r="6"/><path d="M15 15l5 5"/><path d="M8 10h4M10 8v4"/>'
        "</svg>"
    ),
    "proving_ground_project": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<circle cx="10" cy="10" r="6"/><path d="M15 15l5 5"/><path d="M10 7v6"/>'
        "</svg>"
    ),
    "facility": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<path d="M4 21V9l8-6 8 6v12"/><path d="M9 21v-6h6v6"/>'
        "</svg>"
    ),
    "facility_upgrade": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<path d="M4 21V9l8-6 8 6v12"/><path d="M9 21v-6h6v6"/><path d="M12 2v4"/>'
        "</svg>"
    ),
    "soldier_class": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<circle cx="12" cy="7" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>'
        "</svg>"
    ),
    "ability": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<path d="M12 2l3 7 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z"/>'
        "</svg>"
    ),
    "technology": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>'
        "</svg>"
    ),
    "enemy": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<path d="M5 9a7 7 0 0114 0v6a2 2 0 01-2 2H7a2 2 0 01-2-2z"/><circle cx="9.5" cy="11.5" r="1"/><circle cx="14.5" cy="11.5" r="1"/>'
        "</svg>"
    ),
    "mission": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<path d="M12 2a7 7 0 017 7c0 5-7 13-7 13S5 14 5 9a7 7 0 017-7z"/><circle cx="12" cy="9" r="2"/>'
        "</svg>"
    ),
    "strategy_upgrade": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>'
        "</svg>"
    ),
    "covert_action": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<path d="M12 2l9 4v6c0 5-4 9-9 10-5-1-9-5-9-10V6z"/><path d="M9 12l2 2 4-4"/>'
        "</svg>"
    ),
    "faction": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<circle cx="7" cy="8" r="2.5"/><circle cx="17" cy="8" r="2.5"/><path d="M2 20c0-3 2.5-5 5-5s5 2 5 5M12 20c0-3 2.5-5 5-5s5 2 5 5"/>'
        "</svg>"
    ),
    "avenger_room_state": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>'
        "</svg>"
    ),
    "dark_event": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">'
        '<path d="M12 3v6l5 3-5 3v6"/><circle cx="12" cy="12" r="9"/>'
        "</svg>"
    ),
}


def fallback_glyph_for_type(object_type: CatalogObjectType | None) -> str:
    """Return an inline SVG glyph markup for objects without an image.

    Prefers a per-type-slug glyph (`_GLYPHS_BY_TYPE_SLUG`) so the catalog
    grid/detail pages read differently at a glance between e.g. weapons and
    facilities even with no uploaded image (`FR-AST-5`), falling back to
    one generic glyph for any slug not listed (including pack-introduced
    types, `ADR-0003`). `catalog_object_types.default_asset_id` remains in
    the schema for a future "upload a real custom glyph per type" path;
    this function is the code-level fallback used until/unless a type sets
    that column.
    """
    if object_type is not None and object_type.slug in _GLYPHS_BY_TYPE_SLUG:
        return _GLYPHS_BY_TYPE_SLUG[object_type.slug]
    return _DEFAULT_GLYPH


__all__ = [
    "AssetValidationError",
    "fallback_glyph_for_type",
    "read_asset_bytes",
    "upload_image_for_object",
]
