"""Migration/schema tests (ADR-0014, supersedes ADR-0007's bespoke test;
NFR-2: a test applies all migrations to an empty DB and compares with the
documented schema).

The `app`/`db` fixtures already apply every migration to a fresh temp-file
database (tests/conftest.py); this module asserts the resulting structure
matches what DATABASE_SCHEMA.md documents.
"""
from __future__ import annotations

from sqlalchemy import inspect

# Every table named in DATABASE_SCHEMA.md §1-5 (schema_migrations replaced by
# Alembic's own alembic_version per ADR-0014).
EXPECTED_TABLES = {
    "content_sources",
    "content_source_relations",
    "catalog_object_types",
    "catalog_attribute_definitions",
    "catalog_objects",
    "catalog_object_tags",
    "catalog_relationship_types",
    "catalog_relationships",
    "assets",
    "object_assets",
    "campaigns",
    "campaign_content_sources",
    "campaign_object_state",
    "campaign_events",
    "campaign_plans",
    "campaign_plan_items",
    "campaign_soldiers",
    "campaign_soldier_loadouts",
    "campaign_strategy_profiles",
    "strategy_profiles",
    "strategy_recommendations",
    "alembic_version",
}


def test_all_documented_tables_exist(app, db):
    with app.app_context():
        inspector = inspect(db.engine)
        actual_tables = set(inspector.get_table_names())
    assert EXPECTED_TABLES <= actual_tables
    assert actual_tables == EXPECTED_TABLES


def test_campaigns_table_has_documented_columns(app, db):
    with app.app_context():
        inspector = inspect(db.engine)
        columns = {c["name"] for c in inspector.get_columns("campaigns")}
    expected = {
        "id", "name", "game_profile_slug", "difficulty", "is_ironman",
        "notes", "status", "effective_catalog_version", "created_at", "updated_at",
    }
    assert columns == expected


def test_campaign_object_state_unique_natural_key(app, db):
    with app.app_context():
        inspector = inspect(db.engine)
        uniques = inspector.get_unique_constraints("campaign_object_state")
        natural_keys = [set(u["column_names"]) for u in uniques]
    assert {"campaign_id", "catalog_object_id"} in natural_keys


def test_catalog_objects_unique_source_template_key(app, db):
    with app.app_context():
        inspector = inspect(db.engine)
        uniques = inspector.get_unique_constraints("catalog_objects")
        unique_sets = [set(u["column_names"]) for u in uniques]
    assert {"source_id", "template_key"} in unique_sets


def test_foreign_key_delete_rules_match_schema_doc(app, db):
    """Spot-check the ON DELETE behaviours called out in DATABASE_SCHEMA.md."""
    with app.app_context():
        inspector = inspect(db.engine)

        campaigns_fks = {
            fk["referred_table"]: fk["options"].get("ondelete")
            for fk in inspector.get_foreign_keys("campaign_object_state")
        }
        assert campaigns_fks["campaigns"] == "CASCADE"
        assert campaigns_fks["catalog_objects"] == "RESTRICT"

        catalog_objects_fks = {
            (fk["referred_table"], tuple(fk["constrained_columns"])): fk["options"].get("ondelete")
            for fk in inspector.get_foreign_keys("catalog_objects")
        }
        assert catalog_objects_fks[("content_sources", ("source_id",))] == "CASCADE"
        assert catalog_objects_fks[("catalog_object_types", ("type_id",))] == "RESTRICT"
