"""Structural campaign-isolation check (ADR-0010, DOMAIN_MODEL.md §10 invariant 1):
no catalog_*/assets table may have a foreign key into any campaign_* table.
"""
from __future__ import annotations

from sqlalchemy import inspect

CATALOG_AND_ASSET_TABLES = {
    "content_sources",
    "content_source_relations",
    "catalog_object_types",
    "catalog_attribute_definitions",
    "catalog_objects",
    "catalog_object_tags",
    "catalog_relationship_types",
    "catalog_relationships",
    "catalog_prerequisite_groups",
    "catalog_prerequisite_group_members",
    "catalog_soldier_stat_profiles",
    "assets",
    "object_assets",
    "strategy_profiles",
    "strategy_recommendations",
}


def test_no_catalog_table_references_a_campaign_table(app, db):
    with app.app_context():
        inspector = inspect(db.engine)
        offending = []
        for table in CATALOG_AND_ASSET_TABLES:
            for fk in inspector.get_foreign_keys(table):
                if fk["referred_table"].startswith("campaign"):
                    offending.append((table, fk["referred_table"]))
    assert offending == [], f"Catalog tables referencing campaign tables: {offending}"


def test_every_campaign_table_has_a_campaign_id_cascade(app, db):
    """Every campaign_* table other than the `campaigns` root itself cascades
    from `campaigns`, directly or (for child-of-child tables) via its own
    direct parent, per ADR-0010 and DATABASE_SCHEMA.md §7."""
    with app.app_context():
        inspector = inspect(db.engine)
        campaign_tables = [
            t
            for t in inspector.get_table_names()
            if t.startswith("campaign") and t != "campaigns"
        ]
        for table in campaign_tables:
            fks = inspector.get_foreign_keys(table)
            cascades_to_a_campaign_owned_table = any(
                fk["options"].get("ondelete") == "CASCADE"
                and (fk["referred_table"] == "campaigns" or fk["referred_table"].startswith("campaign"))
                for fk in fks
            )
            assert cascades_to_a_campaign_owned_table, (
                f"{table} has no CASCADE foreign key back to campaigns/campaign_* -- "
                "deleting a campaign would not clean it up."
            )
