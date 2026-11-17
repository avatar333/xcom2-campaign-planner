"""`flask seed-dev` service tests: idempotency and minimal foundational scope
(DATABASE_SCHEMA.md §9 -- must seed content sources + builtin types, must NOT
seed any XCOM game objects)."""
from __future__ import annotations

from app.models import CatalogObject, CatalogObjectType, CatalogRelationshipType, ContentSource
from app.services.seed import seed_dev


def test_seed_dev_creates_foundational_content_sources(app, db):
    with app.app_context():
        seed_dev()
        slugs = {s.slug for s in ContentSource.query.all()}
        assert slugs == {"xcom2-base", "wotc", "lwotc", "user-local"}


def test_seed_dev_creates_builtin_object_and_relationship_types(app, db):
    with app.app_context():
        seed_dev()
        assert CatalogObjectType.query.filter_by(is_builtin=True).count() == 21
        assert CatalogRelationshipType.query.filter_by(is_builtin=True).count() == 19


def test_seed_dev_does_not_create_any_catalog_objects(app, db):
    """Explicit Phase 2 scope boundary: no XCOM game objects are seeded."""
    with app.app_context():
        seed_dev()
        assert CatalogObject.query.count() == 0


def test_seed_dev_is_idempotent(app, db):
    with app.app_context():
        first = seed_dev()
        second = seed_dev()

        assert first["content_sources"] == 4
        assert second["content_sources"] == 0
        assert second["object_types"] == 0
        assert second["relationship_types"] == 0
        assert ContentSource.query.count() == 4


def test_relationship_types_have_inverse_wired(app, db):
    with app.app_context():
        seed_dev()
        prerequisite = CatalogRelationshipType.query.filter_by(slug="prerequisite").first()
        unlocks = CatalogRelationshipType.query.filter_by(slug="unlocks").first()
        assert prerequisite.inverse_type_id == unlocks.id
        assert unlocks.inverse_type_id == prerequisite.id
