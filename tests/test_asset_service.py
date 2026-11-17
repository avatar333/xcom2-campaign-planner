"""Asset upload orchestration tests (FR-AST-1/2, wiring storage.py to the
`assets`/`object_assets` repository and catalog object primary_asset_id)."""
from __future__ import annotations

import pytest

from app.models import Asset, CatalogObjectType, ContentSource, ObjectAsset
from app.services import asset_service
from app.services import catalog_objects as objects_service
from app.services.asset_service import AssetValidationError
from tests.helpers import make_png_bytes, make_text_file_bytes


def _make_object(app):
    source = ContentSource.query.filter_by(slug="user-local").first()
    obj_type = CatalogObjectType.query.filter_by(slug="weapon").first()
    return objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Illustrated Weapon")


def test_upload_image_for_object(app, seeded_db):
    with app.app_context():
        obj = _make_object(app)

        asset = asset_service.upload_image_for_object(obj.id, role="icon", raw_bytes=make_png_bytes())

        assert asset.id is not None
        assert asset.origin == "upload"
        link = ObjectAsset.query.filter_by(object_id=obj.id, asset_id=asset.id, role="icon").first()
        assert link is not None


def test_upload_image_sets_primary_asset_id_when_unset(app, seeded_db):
    with app.app_context():
        obj = _make_object(app)
        assert obj.primary_asset_id is None

        asset = asset_service.upload_image_for_object(obj.id, role="icon", raw_bytes=make_png_bytes())

        from app.repositories.catalog_objects import get_object

        reloaded = get_object(obj.id)
        assert reloaded.primary_asset_id == asset.id


def test_upload_invalid_image_raises_and_does_not_create_asset(app, seeded_db):
    with app.app_context():
        obj = _make_object(app)

        with pytest.raises(AssetValidationError):
            asset_service.upload_image_for_object(obj.id, role="icon", raw_bytes=make_text_file_bytes())

        assert Asset.query.count() == 0


def test_uploading_same_image_twice_deduplicates_asset_row(app, seeded_db):
    with app.app_context():
        obj_a = _make_object(app)
        source = ContentSource.query.filter_by(slug="user-local").first()
        obj_type = CatalogObjectType.query.filter_by(slug="weapon").first()
        obj_b = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Second Weapon")

        png_bytes = make_png_bytes()
        asset_a = asset_service.upload_image_for_object(obj_a.id, role="icon", raw_bytes=png_bytes)
        asset_b = asset_service.upload_image_for_object(obj_b.id, role="icon", raw_bytes=png_bytes)

        assert asset_a.id == asset_b.id
        assert Asset.query.count() == 1


def test_read_asset_bytes_roundtrip(app, seeded_db):
    with app.app_context():
        obj = _make_object(app)
        asset = asset_service.upload_image_for_object(obj.id, role="icon", raw_bytes=make_png_bytes())

        data = asset_service.read_asset_bytes(asset)
        assert data.startswith(b"\x89PNG")


def test_fallback_glyph_for_type_returns_svg_markup(app, seeded_db):
    with app.app_context():
        obj_type = CatalogObjectType.query.filter_by(slug="weapon").first()
        glyph = asset_service.fallback_glyph_for_type(obj_type)
        assert "<svg" in glyph


def test_fallback_glyph_differs_between_distinct_types(app, seeded_db):
    """Phase 4 FR-AST-5 upgrade: glyphs are per-type, not one shared shape."""
    with app.app_context():
        weapon_type = CatalogObjectType.query.filter_by(slug="weapon").first()
        facility_type = CatalogObjectType.query.filter_by(slug="facility").first()

        weapon_glyph = asset_service.fallback_glyph_for_type(weapon_type)
        facility_glyph = asset_service.fallback_glyph_for_type(facility_type)

        assert weapon_glyph != facility_glyph


def test_fallback_glyph_falls_back_to_generic_for_unknown_or_none_type(app, seeded_db):
    with app.app_context():
        generic_for_none = asset_service.fallback_glyph_for_type(None)
        assert "<svg" in generic_for_none

        unknown_type = CatalogObjectType(slug="totally-unseen-type", name="Unseen", is_builtin=False)
        generic_for_unknown = asset_service.fallback_glyph_for_type(unknown_type)
        assert generic_for_unknown == generic_for_none


def test_fetch_image_for_object_rejects_private_host(app, seeded_db):
    """FR-AST-4 SSRF guard: internal/private hosts are refused before any
    network request is attempted."""
    with app.app_context():
        obj = _make_object(app)
        with pytest.raises(AssetValidationError):
            asset_service.fetch_image_for_object(obj.id, role="icon", url="http://127.0.0.1/evil.png")


def test_fetch_image_for_object_rejects_non_http_scheme(app, seeded_db):
    with app.app_context():
        obj = _make_object(app)
        with pytest.raises(AssetValidationError):
            asset_service.fetch_image_for_object(obj.id, role="icon", url="file:///etc/passwd")


def test_fetch_image_for_object_downloads_and_stores_locally(app, seeded_db, monkeypatch):
    with app.app_context():
        obj = _make_object(app)

        class _FakeResponse:
            def __init__(self, data: bytes):
                self._data = data

            def read(self, _n=-1):
                return self._data

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

        png_bytes = make_png_bytes()
        monkeypatch.setattr(
            asset_service, "_assert_safe_fetch_url", lambda url: None
        )

        def _fake_urlopen(request, timeout):
            return _FakeResponse(png_bytes)

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        asset = asset_service.fetch_image_for_object(
            obj.id, role="icon", url="https://example.com/thing.png", attribution_text="Example"
        )

        assert asset.origin == "fetched_url"
        assert asset.source_url == "https://example.com/thing.png"

        from app.repositories.catalog_objects import get_object

        reloaded = get_object(obj.id)
        assert reloaded.primary_asset_id == asset.id

        data = asset_service.read_asset_bytes(asset)
        assert data.startswith(b"\x89PNG")


def test_fetch_image_for_object_rejects_oversized_response(app, seeded_db, monkeypatch):
    with app.app_context():
        obj = _make_object(app)

        class _FakeResponse:
            def __init__(self, data: bytes):
                self._data = data

            def read(self, _n=-1):
                return self._data

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

        monkeypatch.setattr(asset_service, "_assert_safe_fetch_url", lambda url: None)
        oversized = b"x" * (asset_service.MAX_FETCH_MB * 1024 * 1024 + 10)

        def _fake_urlopen(request, timeout):
            return _FakeResponse(oversized)

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        with pytest.raises(AssetValidationError):
            asset_service.fetch_image_for_object(obj.id, role="icon", url="https://example.com/huge.png")
