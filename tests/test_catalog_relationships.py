"""Catalog relationship CRUD + validation tests (FR-CAT-3, ADR-0004)."""
from __future__ import annotations

import pytest

from app.models import CatalogObjectType, CatalogRelationshipType, ContentSource
from app.repositories import catalog_relationships as relationships_repo
from app.services import catalog_objects as objects_service
from app.services import catalog_relationships as relationships_service
from app.services.catalog_relationships import RelationshipValidationError


def _make_two_objects(app):
    source = ContentSource.query.filter_by(slug="user-local").first()
    obj_type = CatalogObjectType.query.filter_by(slug="weapon").first()
    obj_a = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Object A")
    obj_b = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Object B")
    return source, obj_a, obj_b


def test_create_relationship(app, seeded_db):
    with app.app_context():
        source, obj_a, obj_b = _make_two_objects(app)
        rel_type = CatalogRelationshipType.query.filter_by(slug="prerequisite").first()

        rel = relationships_service.create_relationship(
            relationship_type=rel_type, source_object=obj_a, target_object=obj_b, source_pack_id=source.id
        )

        assert rel.source_object_id == obj_a.id
        assert rel.target_object_id == obj_b.id
        assert rel.relationship_type_id == rel_type.id


def test_relationships_for_object_outbound_and_inbound(app, seeded_db):
    with app.app_context():
        source, obj_a, obj_b = _make_two_objects(app)
        rel_type = CatalogRelationshipType.query.filter_by(slug="prerequisite").first()
        relationships_service.create_relationship(
            relationship_type=rel_type, source_object=obj_a, target_object=obj_b, source_pack_id=source.id
        )

        outbound, inbound = relationships_repo.list_for_object(obj_a.id)
        assert len(outbound) == 1
        assert len(inbound) == 0

        outbound_b, inbound_b = relationships_repo.list_for_object(obj_b.id)
        assert len(outbound_b) == 0
        assert len(inbound_b) == 1


def test_self_relationship_is_rejected(app, seeded_db):
    with app.app_context():
        source, obj_a, _obj_b = _make_two_objects(app)
        rel_type = CatalogRelationshipType.query.filter_by(slug="compatible_with").first()
        with pytest.raises(RelationshipValidationError):
            relationships_service.create_relationship(
                relationship_type=rel_type, source_object=obj_a, target_object=obj_a, source_pack_id=source.id
            )


def test_exact_duplicate_relationship_is_rejected(app, seeded_db):
    with app.app_context():
        source, obj_a, obj_b = _make_two_objects(app)
        rel_type = CatalogRelationshipType.query.filter_by(slug="compatible_with").first()
        relationships_service.create_relationship(
            relationship_type=rel_type, source_object=obj_a, target_object=obj_b, source_pack_id=source.id
        )
        with pytest.raises(RelationshipValidationError):
            relationships_service.create_relationship(
                relationship_type=rel_type, source_object=obj_a, target_object=obj_b, source_pack_id=source.id
            )


def test_different_relationship_types_between_same_pair_are_allowed(app, seeded_db):
    with app.app_context():
        source, obj_a, obj_b = _make_two_objects(app)
        compatible = CatalogRelationshipType.query.filter_by(slug="compatible_with").first()
        prerequisite = CatalogRelationshipType.query.filter_by(slug="prerequisite").first()

        relationships_service.create_relationship(
            relationship_type=compatible, source_object=obj_a, target_object=obj_b, source_pack_id=source.id
        )
        # Should not raise -- different relationship type, same object pair.
        relationships_service.create_relationship(
            relationship_type=prerequisite, source_object=obj_a, target_object=obj_b, source_pack_id=source.id
        )

        outbound, _inbound = relationships_repo.list_for_object(obj_a.id)
        assert len(outbound) == 2


def test_direct_two_node_cycle_is_rejected_for_prerequisite(app, seeded_db):
    """prerequisite is in the acyclic-required subset (ADR-0004 addendum):
    A requires B, then B requires A directly, must be rejected."""
    with app.app_context():
        source, obj_a, obj_b = _make_two_objects(app)
        rel_type = CatalogRelationshipType.query.filter_by(slug="prerequisite").first()

        relationships_service.create_relationship(
            relationship_type=rel_type, source_object=obj_a, target_object=obj_b, source_pack_id=source.id
        )

        with pytest.raises(RelationshipValidationError):
            relationships_service.create_relationship(
                relationship_type=rel_type, source_object=obj_b, target_object=obj_a, source_pack_id=source.id
            )


def test_direct_two_node_cycle_is_allowed_for_non_acyclic_kind(app, seeded_db):
    """compatible_with has no acyclic requirement -- a symmetric pair of
    edges (even if redundant) must not be rejected as a 'cycle'."""
    with app.app_context():
        source, obj_a, obj_b = _make_two_objects(app)
        rel_type = CatalogRelationshipType.query.filter_by(slug="compatible_with").first()

        relationships_service.create_relationship(
            relationship_type=rel_type, source_object=obj_a, target_object=obj_b, source_pack_id=source.id
        )
        # Should not raise.
        relationships_service.create_relationship(
            relationship_type=rel_type, source_object=obj_b, target_object=obj_a, source_pack_id=source.id
        )


def test_quantified_relationship_requires_amount(app, seeded_db):
    with app.app_context():
        source, obj_a, obj_b = _make_two_objects(app)
        rel_type = CatalogRelationshipType.query.filter_by(slug="requires_resource").first()
        assert rel_type.is_quantified is True

        with pytest.raises(RelationshipValidationError):
            relationships_service.create_relationship(
                relationship_type=rel_type, source_object=obj_a, target_object=obj_b, source_pack_id=source.id
            )

        # With an amount, it succeeds.
        rel = relationships_service.create_relationship(
            relationship_type=rel_type, source_object=obj_a, target_object=obj_b,
            amount=2, unit="units", source_pack_id=source.id,
        )
        assert rel.amount == 2


def test_or_group_relationships(app, seeded_db):
    """Two prerequisite edges sharing a group_key with group_logic=OR
    represent an alternative-path requirement (ARCHITECTURE.md §5.3 example)."""
    with app.app_context():
        source = ContentSource.query.filter_by(slug="user-local").first()
        obj_type = CatalogObjectType.query.filter_by(slug="research_project").first()
        plasma_rifle = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Plasma Rifle")
        adv_laser = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Advanced Laser Weapons")
        adv_coilgun = objects_service.create_object(source_id=source.id, type_id=obj_type.id, name="Advanced Coilguns")

        rel_type = CatalogRelationshipType.query.filter_by(slug="prerequisite").first()
        relationships_service.create_relationship(
            relationship_type=rel_type, source_object=adv_laser, target_object=plasma_rifle,
            group_key="plasma-rifle-path", source_pack_id=source.id,
        )
        relationships_service.create_relationship(
            relationship_type=rel_type, source_object=adv_coilgun, target_object=plasma_rifle,
            group_key="plasma-rifle-path", source_pack_id=source.id,
        )

        _outbound, inbound = relationships_repo.list_for_object(plasma_rifle.id)
        assert len(inbound) == 2
        assert all(r.group_key == "plasma-rifle-path" for r in inbound)
        assert all(r.group_logic == "OR" for r in inbound)


def test_delete_relationship(app, seeded_db):
    with app.app_context():
        source, obj_a, obj_b = _make_two_objects(app)
        rel_type = CatalogRelationshipType.query.filter_by(slug="compatible_with").first()
        rel = relationships_service.create_relationship(
            relationship_type=rel_type, source_object=obj_a, target_object=obj_b, source_pack_id=source.id
        )
        rel_id = rel.id

        relationships_service.delete_relationship(rel)

        assert relationships_repo.get_relationship(rel_id) is None
