"""Game profile presets (DOMAIN_MODEL.md §1 "Game Profile", IMPLEMENTATION_PLAN.md Phase 2).

Profiles are data files under `content/profiles/*.json`, not Python branches,
so a new profile (e.g. a future community pack bundle) is addable without a
code change -- the same "extensible by data" principle as content sources
themselves (ADR-0003).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_PROFILES_DIR = Path(__file__).resolve().parent.parent.parent / "content" / "profiles"


@dataclass(frozen=True)
class GameProfile:
    slug: str
    name: str
    description: str
    default_sources: tuple[str, ...]


def _load_profile(path: Path) -> GameProfile:
    data = json.loads(path.read_text())
    return GameProfile(
        slug=data["slug"],
        name=data["name"],
        description=data.get("description", ""),
        default_sources=tuple(data.get("default_sources", [])),
    )


def list_game_profiles() -> list[GameProfile]:
    if not _PROFILES_DIR.is_dir():
        return []
    profiles = [_load_profile(p) for p in sorted(_PROFILES_DIR.glob("*.json"))]
    # Stable, deliberate ordering: standard, then total conversions, then custom last.
    order = {"wotc-standard": 0, "lwotc": 1, "custom": 99}
    return sorted(profiles, key=lambda p: order.get(p.slug, 50))


def get_game_profile(slug: str) -> GameProfile | None:
    for profile in list_game_profiles():
        if profile.slug == slug:
            return profile
    return None
