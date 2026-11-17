"""`flask seed-dev`: minimal foundational/reference records (DATABASE_SCHEMA.md §9).

Explicitly NOT the catalog importer (Phase 3, ARCHITECTURE.md §5) -- this
seeds only:
  1. the four foundational content sources named in the Phase 2 brief
     (xcom2-base, wotc, lwotc, user-local), as provenance anchors with no
     catalog objects attached yet;
  2. the builtin catalog_object_types (DOMAIN_MODEL.md §4 seed list);
  3. the builtin catalog_relationship_types (DOMAIN_MODEL.md §6 seed list).

No XCOM game objects, no relationships between game objects -- that is
explicitly out of scope for this phase.
"""
from __future__ import annotations

from app.extensions import db
from app.models import CatalogObjectType, CatalogRelationshipType, ContentSource
from app.models.base import utc_now_iso

FOUNDATIONAL_CONTENT_SOURCES = [
    {
        "slug": "xcom2-base",
        "name": "XCOM 2",
        "kind": "base",
        "version": "1.0.0",
        "description": "The base XCOM 2 game.",
        "is_builtin": True,
        "is_enabled_by_default": True,
    },
    {
        "slug": "wotc",
        "name": "War of the Chosen",
        "kind": "dlc",
        "version": "1.0.0",
        "description": "The War of the Chosen expansion.",
        "is_builtin": True,
        "is_enabled_by_default": True,
    },
    {
        "slug": "lwotc",
        "name": "Long War of the Chosen",
        "kind": "total_conversion",
        "version": "1.0.0",
        "description": "The LWotC community total-conversion overhaul.",
        "homepage_url": "https://github.com/long-war-2/lwotc",
        "is_builtin": True,
        "is_enabled_by_default": False,
    },
    {
        "slug": "user-local",
        "name": "Custom / User Content",
        "kind": "user",
        "version": "0.0.0",
        "description": "Manually authored custom/mod objects created in this app.",
        "is_builtin": True,
        "is_enabled_by_default": False,
    },
]

# DOMAIN_MODEL.md §4 -- non-exhaustive, extensible-by-data seed list.
BUILTIN_OBJECT_TYPES = [
    ("weapon", "Weapon"),
    ("weapon_upgrade", "Weapon Upgrade"),
    ("armor", "Armor"),
    ("utility_item", "Utility Item"),
    ("ammo", "Ammunition"),
    ("grenade", "Grenade"),
    ("resource", "Resource"),
    ("research_project", "Research Project"),
    ("autopsy_project", "Autopsy Project"),
    ("proving_ground_project", "Proving Ground Project"),
    ("facility", "Facility"),
    ("facility_upgrade", "Facility Upgrade"),
    ("soldier_class", "Soldier Class"),
    ("ability", "Ability"),
    ("technology", "Technology"),
    ("enemy", "Enemy"),
    ("mission", "Mission"),
    ("strategy_upgrade", "Strategy Upgrade"),
    ("covert_action", "Covert Action"),
    ("faction", "Faction"),
    ("dark_event", "Dark Event"),
]

# DOMAIN_MODEL.md §6 -- seed relationship types with their inverse pairing.
BUILTIN_RELATIONSHIP_TYPES = [
    ("prerequisite", "Prerequisite", "unlocks", False),
    ("unlocks", "Unlocks", "prerequisite", False),
    ("upgrades_to", "Upgrades To", "upgraded_from", False),
    ("upgraded_from", "Upgraded From", "upgrades_to", False),
    ("requires_facility", "Requires Facility", "enables", False),
    ("enables", "Enables", "requires_facility", False),
    ("produced_by", "Produced By", "produces", False),
    ("produces", "Produces", "produced_by", False),
    ("requires_resource", "Requires Resource", "consumed_by", True),
    ("consumed_by", "Consumed By", "requires_resource", True),
    ("class_can_equip", "Class Can Equip", "equippable_by_class", False),
    ("equippable_by_class", "Equippable By Class", "class_can_equip", False),
    ("compatible_with", "Compatible With", "compatible_with", False),
    ("replaces", "Replaces", "replaced_by", False),
    ("replaced_by", "Replaced By", "replaces", False),
    ("supersedes", "Supersedes", "superseded_by", False),
    ("superseded_by", "Superseded By", "supersedes", False),
    ("requires_research", "Requires Research", "required_by_research", False),
    ("required_by_research", "Required By Research", "requires_research", False),
]


def seed_content_sources() -> int:
    created = 0
    for fields in FOUNDATIONAL_CONTENT_SOURCES:
        if ContentSource.query.filter_by(slug=fields["slug"]).first() is not None:
            continue
        db.session.add(ContentSource(**fields))
        created += 1
    return created


def seed_object_types() -> int:
    created = 0
    for slug, name in BUILTIN_OBJECT_TYPES:
        if CatalogObjectType.query.filter_by(slug=slug).first() is not None:
            continue
        db.session.add(
            CatalogObjectType(
                slug=slug,
                name=name,
                is_builtin=True,
                created_at=utc_now_iso(),
            )
        )
        created += 1
    return created


def seed_relationship_types() -> int:
    created = 0
    by_slug: dict[str, CatalogRelationshipType] = {
        rt.slug: rt for rt in CatalogRelationshipType.query.all()
    }
    for slug, name, _inverse_slug, is_quantified in BUILTIN_RELATIONSHIP_TYPES:
        if slug in by_slug:
            continue
        rt = CatalogRelationshipType(
            slug=slug, name=name, is_quantified=is_quantified, is_builtin=True
        )
        db.session.add(rt)
        by_slug[slug] = rt
        created += 1
    db.session.flush()

    # Second pass: wire up inverse_type_id now that every row has an id.
    for slug, _name, inverse_slug, _quantified in BUILTIN_RELATIONSHIP_TYPES:
        rt = by_slug.get(slug)
        inverse = by_slug.get(inverse_slug)
        if rt is not None and inverse is not None and rt.inverse_type_id != inverse.id:
            rt.inverse_type_id = inverse.id
    return created


def seed_dev() -> dict[str, int]:
    """Idempotent: safe to run repeatedly, only inserts missing rows."""
    counts = {
        "content_sources": seed_content_sources(),
        "object_types": seed_object_types(),
        "relationship_types": seed_relationship_types(),
    }
    db.session.commit()
    return counts
