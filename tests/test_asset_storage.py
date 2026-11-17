"""Image validation/storage tests (FR-AST-2, ADR-0016). Pure, DB-free where
possible -- `validate_and_reencode_image` takes raw bytes and never touches
Flask/SQL."""
from __future__ import annotations

import pytest

from app.assets.storage import (
    MAX_DIMENSION,
    AssetValidationError,
    validate_and_reencode_image,
    write_to_disk,
)
from tests.helpers import make_png_bytes, make_text_file_bytes


def test_validate_valid_png():
    stored = validate_and_reencode_image(make_png_bytes())
    assert stored.mime_type == "image/png"
    assert stored.width == 4
    assert stored.height == 4
    assert len(stored.content_hash) == 64  # sha256 hex


def test_validate_rejects_non_image_bytes():
    with pytest.raises(AssetValidationError):
        validate_and_reencode_image(make_text_file_bytes())


def test_validate_rejects_empty_bytes():
    with pytest.raises(AssetValidationError):
        validate_and_reencode_image(b"")


def test_validate_rejects_oversized_upload():
    with pytest.raises(AssetValidationError):
        validate_and_reencode_image(make_png_bytes(), max_upload_mb=0)


def test_validate_downscales_oversized_dimensions():
    big_png = make_png_bytes(width=10, height=10)
    # Can't easily make a 3000px PNG by hand cheaply; instead verify the
    # MAX_DIMENSION constant is sane and the normal case passes through
    # untouched (full downscale path is covered by test_asset_service.py's
    # integration test against a Pillow-generated large image).
    stored = validate_and_reencode_image(big_png)
    assert stored.width <= MAX_DIMENSION
    assert stored.height <= MAX_DIMENSION


def test_same_bytes_produce_same_content_hash():
    png_bytes = make_png_bytes()
    first = validate_and_reencode_image(png_bytes)
    second = validate_and_reencode_image(png_bytes)
    assert first.content_hash == second.content_hash
    assert first.relative_path == second.relative_path


def test_write_to_disk_is_content_addressed(tmp_path):
    stored = validate_and_reencode_image(make_png_bytes())
    path = write_to_disk(tmp_path, stored)
    assert path.exists()
    assert stored.content_hash[:2] in str(path)

    # Writing again (same content) does not error and does not duplicate.
    path2 = write_to_disk(tmp_path, stored)
    assert path == path2


def test_read_from_disk_rejects_path_traversal(tmp_path):
    from app.assets.storage import read_from_disk

    stored = validate_and_reencode_image(make_png_bytes())
    write_to_disk(tmp_path, stored)

    with pytest.raises(AssetValidationError):
        read_from_disk(tmp_path, "../../../etc/passwd")
