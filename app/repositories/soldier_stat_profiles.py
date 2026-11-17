"""Soldier stat profile repository (Phase 4, brief "Soldier stat profiles")."""
from __future__ import annotations

from app.extensions import db
from app.models import CatalogSoldierStatProfile


def upsert_profile(
    *, catalog_object_id: int, difficulty: str | None, stat_key: str, **fields
) -> CatalogSoldierStatProfile:
    existing = CatalogSoldierStatProfile.query.filter_by(
        catalog_object_id=catalog_object_id, difficulty=difficulty, stat_key=stat_key
    ).first()
    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
        db.session.flush()
        return existing
    profile = CatalogSoldierStatProfile(
        catalog_object_id=catalog_object_id, difficulty=difficulty, stat_key=stat_key, **fields
    )
    db.session.add(profile)
    db.session.flush()
    return profile


def list_for_object(catalog_object_id: int) -> list[CatalogSoldierStatProfile]:
    return CatalogSoldierStatProfile.query.filter_by(catalog_object_id=catalog_object_id).all()


def get_for_object_and_difficulty(
    catalog_object_id: int, difficulty: str | None
) -> list[CatalogSoldierStatProfile]:
    """Stats that apply to `difficulty`: rows pinned to that exact
    difficulty, falling back to difficulty-agnostic (`difficulty IS NULL`)
    rows for any stat_key not covered by a specific-difficulty row."""
    all_rows = list_for_object(catalog_object_id)
    by_key_specific = {r.stat_key: r for r in all_rows if r.difficulty == difficulty}
    by_key_generic = {r.stat_key: r for r in all_rows if r.difficulty is None}
    merged = {**by_key_generic, **by_key_specific}
    return list(merged.values())
