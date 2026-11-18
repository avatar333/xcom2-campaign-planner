"""Shared helpers for Avenger planner tests: real curated packs plus tiny
synthetic packs for edge cases (duplicates, OR groups, ruleset overrides)."""
from __future__ import annotations

import json
from pathlib import Path


def import_real_packs() -> None:
    from app.cli import _discover_pack_slugs, _pack_dir_for
    from app.services.content_import import import_pack

    for slug in _discover_pack_slugs():
        import_pack(str(_pack_dir_for(slug)))


def write_pack(
    root: Path,
    slug: str,
    *,
    objects: list[dict],
    relationships: list[dict] | None = None,
    prerequisite_groups: list[dict] | None = None,
    object_types: list[dict] | None = None,
    requires: list[str] | None = None,
) -> Path:
    pack_dir = root / slug
    pack_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "slug": slug,
        "name": slug,
        "kind": "workshop_mod",
        "version": "1.0.0",
        "requires": requires or [],
        "extends": requires or [],
    }
    (pack_dir / "pack.json").write_text(json.dumps(manifest), encoding="utf-8")
    (pack_dir / "objects.json").write_text(json.dumps(objects), encoding="utf-8")
    if relationships is not None:
        (pack_dir / "relationships.json").write_text(json.dumps(relationships), encoding="utf-8")
    if prerequisite_groups is not None:
        (pack_dir / "prerequisite_groups.json").write_text(
            json.dumps(prerequisite_groups), encoding="utf-8"
        )
    if object_types is not None:
        (pack_dir / "object_types.json").write_text(json.dumps(object_types), encoding="utf-8")
    return pack_dir


def facility(key: str, name: str, **attributes) -> dict:
    attrs = {"buildable_in_avenger_grid": True, "allows_duplicates": False}
    attrs.update(attributes)
    return {"template_key": key, "type": "facility", "name": name, "attributes": attrs}


def new_campaign(name: str, profile: str | None = "wotc-standard"):
    from app.services import campaign_lifecycle

    return campaign_lifecycle.create_campaign(name=name, game_profile_slug=profile)


def attach_source(campaign, slug: str) -> None:
    from app.repositories import content_sources as sources_repo
    from app.services import campaign_sources as campaign_sources_service

    campaign_sources_service.add_source_to_campaign(campaign, sources_repo.get_content_source_by_slug(slug))


def facility_id(campaign, name: str) -> int:
    from app.services import avenger

    return next(o.object_id for o in avenger.list_facility_options(campaign) if o.name == name)


def room_id(campaign, name: str) -> int:
    from app.services import avenger

    return next(o.object_id for o in avenger.list_room_options(campaign) if o.name == name)
