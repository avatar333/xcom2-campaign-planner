"""Content-addressed local asset storage (ARCHITECTURE.md §8, FR-AST-1/2/3/5/6).

Design notes (ADR-0006, ADR-0008):
- Every stored file lives at ``<ASSETS_PATH>/<hash[:2]>/<hash>.<ext>`` --
  content-addressed, so identical uploads/pack assets are de-duplicated and
  re-importing an unchanged pack costs no extra disk I/O.
- Uploaded images are re-encoded through Pillow (never saved byte-for-byte
  from the client), which both verifies the file is a genuine image (not a
  renamed script) and strips embedded metadata/EXIF/payloads.
- The DB row (``assets``) is the only thing callers deal with; the storage
  path is never exposed to templates -- ``routes/assets.py`` serves bytes
  from ``asset.storage_path`` by id.
- An object's image can be replaced by attaching a new asset without ever
  changing the object's own id (`object_assets` is a separate join table).
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

ALLOWED_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/webp": "webp",
    "image/gif": "gif",
}
# Pillow's own format names, used to double check what it actually decoded.
ALLOWED_PIL_FORMATS = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp", "GIF": "gif"}

MAX_DIMENSION = 2048  # px, longest side after re-encode -- keeps pages light.
DEFAULT_MAX_UPLOAD_MB = 8


class AssetValidationError(ValueError):
    """Raised when an uploaded/fetched image fails validation (FR-AST-2)."""


@dataclass(frozen=True)
class StoredImage:
    """Result of validating + re-encoding image bytes, before DB insertion."""

    content_hash: str
    relative_path: str
    mime_type: str
    width: int
    height: int
    data: bytes


def _extension_for(pil_format: str) -> str:
    return "jpg" if pil_format == "JPEG" else ALLOWED_PIL_FORMATS[pil_format].lower()


def validate_and_reencode_image(raw_bytes: bytes, *, max_upload_mb: int = DEFAULT_MAX_UPLOAD_MB) -> StoredImage:
    """Validate image bytes and re-encode them into a safe, size-capped form.

    Raises `AssetValidationError` for anything that is not a genuine,
    supported raster image, or that exceeds the configured size limit.
    Re-encoding (rather than storing the uploaded bytes verbatim) is what
    strips EXIF/embedded metadata and rules out polyglot files that merely
    *look* like an image to a naive extension/content-type check.
    """
    max_bytes = max_upload_mb * 1024 * 1024
    if len(raw_bytes) == 0:
        raise AssetValidationError("The uploaded file is empty.")
    if len(raw_bytes) > max_bytes:
        raise AssetValidationError(
            f"File is too large ({len(raw_bytes) / 1024 / 1024:.1f} MB); "
            f"the limit is {max_upload_mb} MB."
        )

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img.verify()
        # `verify()` leaves the image unusable for re-encoding; reopen fresh.
        with Image.open(io.BytesIO(raw_bytes)) as img:
            pil_format = img.format
            if pil_format not in ALLOWED_PIL_FORMATS:
                raise AssetValidationError(
                    f"Unsupported image format '{pil_format}'. "
                    "Allowed formats: PNG, JPEG, WebP, GIF."
                )

            is_animated = getattr(img, "is_animated", False)
            if pil_format == "GIF" and is_animated:
                # Re-encoding would collapse animation; keep the original
                # bytes for GIFs but we've already verified it's a real GIF.
                img.seek(0)
                frame = img.convert("RGBA")
            else:
                frame = img.convert("RGBA") if img.mode in ("P", "LA") else img.copy()

            width, height = frame.size
            if width <= 0 or height <= 0:
                raise AssetValidationError("Image has invalid dimensions.")

            if max(width, height) > MAX_DIMENSION:
                frame.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
                width, height = frame.size

            buffer = io.BytesIO()
            if pil_format == "GIF" and is_animated:
                # Preserve the original animated GIF bytes verbatim rather
                # than collapsing to one frame -- still validated above.
                buffer.write(raw_bytes)
                out_bytes = buffer.getvalue()
            elif pil_format == "JPEG":
                frame.convert("RGB").save(buffer, format="JPEG", quality=88, optimize=True)
                out_bytes = buffer.getvalue()
            elif pil_format == "WEBP":
                frame.save(buffer, format="WEBP", quality=88)
                out_bytes = buffer.getvalue()
            else:
                frame.save(buffer, format="PNG", optimize=True)
                out_bytes = buffer.getvalue()
    except AssetValidationError:
        raise
    except UnidentifiedImageError as exc:
        raise AssetValidationError("The uploaded file is not a recognizable image.") from exc
    except Exception as exc:
        raise AssetValidationError(f"Could not process image: {exc}") from exc

    ext = _extension_for(pil_format)
    content_hash = hashlib.sha256(out_bytes).hexdigest()
    relative_path = f"{content_hash[:2]}/{content_hash}.{ext}"
    mime_type = next(mt for mt, e in ALLOWED_CONTENT_TYPES.items() if e == ext)

    return StoredImage(
        content_hash=content_hash,
        relative_path=relative_path,
        mime_type=mime_type,
        width=width,
        height=height,
        data=out_bytes,
    )


def write_to_disk(assets_root: str | Path, stored: StoredImage) -> Path:
    """Write validated image bytes to the content-addressed path.

    Safe by construction: the path is derived entirely from the sha256 hash
    computed in `validate_and_reencode_image`, never from any user-supplied
    filename, so there is no path-traversal surface here.
    """
    root = Path(assets_root)
    full_path = root / stored.relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    if not full_path.exists():
        full_path.write_bytes(stored.data)
    return full_path


def read_from_disk(assets_root: str | Path, relative_path: str) -> bytes:
    root = Path(assets_root).resolve()
    full_path = (root / relative_path).resolve()
    if root not in full_path.parents and full_path != root:
        raise AssetValidationError("Invalid asset path.")
    return full_path.read_bytes()
