"""Asset DB repository (FR-AST-1..6). File I/O lives in `app/assets/storage.py`;
this module only persists/reads the `assets`/`object_assets` rows."""
from __future__ import annotations

from app.extensions import db
from app.models import Asset, ObjectAsset


def get_asset(asset_id: int) -> Asset | None:
    return db.session.get(Asset, asset_id)


def get_by_content_hash(content_hash: str) -> Asset | None:
    return Asset.query.filter_by(content_hash=content_hash).first()


def create_asset(**fields) -> Asset:
    asset = Asset(**fields)
    db.session.add(asset)
    db.session.commit()
    return asset


def attach_to_object(object_id: int, asset_id: int, role: str, *, sort_order: int = 0) -> ObjectAsset:
    existing = ObjectAsset.query.filter_by(object_id=object_id, asset_id=asset_id, role=role).first()
    if existing is not None:
        return existing
    link = ObjectAsset(object_id=object_id, asset_id=asset_id, role=role, sort_order=sort_order)
    db.session.add(link)
    db.session.commit()
    return link


def list_for_object(object_id: int) -> list[ObjectAsset]:
    return (
        ObjectAsset.query.filter_by(object_id=object_id)
        .order_by(ObjectAsset.role.asc(), ObjectAsset.sort_order.asc())
        .all()
    )


def detach_from_object(object_id: int, asset_id: int, role: str) -> None:
    ObjectAsset.query.filter_by(object_id=object_id, asset_id=asset_id, role=role).delete()
    db.session.commit()
