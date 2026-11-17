"""Catalog interchange import/export tests (FR-SRC-7/8, ADR-0017): document
(de)serialization, dry-run validation, conflict detection, round trip."""
from __future__ import annotations

from app.importer.document import (
    CatalogDocument,
    ObjectRecord,
    RelationshipRecord,
    SourceRef,
    from_json,
    to_json,
)
from app.importer.validator import validate_document
from app.services import catalog_import


def _sample_document() -> CatalogDocument:
    return CatalogDocument(
        content_sources=[SourceRef(slug="my-mod", name="My Mod", kind="workshop_mod")],
        object_types=[],
        objects=[
            ObjectRecord(source_slug="my-mod", template_key="Rifle1", type_slug="weapon", name="Example Rifle"),
            ObjectRecord(source_slug="my-mod", template_key="Ammo1", type_slug="ammo", name="Example Ammo"),
        ],
        relationships=[
            RelationshipRecord(
                type_slug="compatible_with",
                source_slug="my-mod", source_template_key="Rifle1",
                target_slug="my-mod", target_template_key="Ammo1",
            )
        ],
    )


# ---- Document (de)serialization ----


def test_to_json_and_from_json_round_trip():
    document = _sample_document()
    serialized = to_json(document)
    parsed = from_json(serialized)

    assert parsed.format == document.format
    assert parsed.version == document.version
    assert len(parsed.objects) == 2
    assert parsed.objects[0].template_key == "Rifle1"
    assert len(parsed.relationships) == 1


def test_from_json_rejects_wrong_format_name():
    import pytest

    from app.importer.document import InterchangeFormatError

    with pytest.raises(InterchangeFormatError):
        from_json('{"format": "something-else", "version": 1}')


def test_from_json_rejects_future_version():
    import pytest

    from app.importer.document import InterchangeFormatError

    with pytest.raises(InterchangeFormatError):
        from_json('{"format": "xcom2-campaign-planner.catalog-export", "version": 999}')


def test_from_json_rejects_invalid_json():
    import pytest

    from app.importer.document import InterchangeFormatError

    with pytest.raises(InterchangeFormatError):
        from_json("not json at all {{{")


# ---- Dry-run validation (DB-backed: checks against existing rows) ----


def test_validate_document_reports_no_errors_for_clean_document(app, seeded_db):
    with app.app_context():
        document = _sample_document()
        report = validate_document(document)

        assert report.is_valid
        assert report.new_sources == 1
        assert report.new_objects == 2
        assert report.new_relationships == 1


def test_validate_document_rejects_self_relationship(app, seeded_db):
    with app.app_context():
        document = CatalogDocument(
            content_sources=[SourceRef(slug="my-mod", name="My Mod", kind="workshop_mod")],
            objects=[ObjectRecord(source_slug="my-mod", template_key="X", type_slug="weapon", name="X")],
            relationships=[
                RelationshipRecord(
                    type_slug="compatible_with",
                    source_slug="my-mod", source_template_key="X",
                    target_slug="my-mod", target_template_key="X",
                )
            ],
        )

        report = validate_document(document)

        assert not report.is_valid


def test_validate_document_rejects_unknown_object_type(app, seeded_db):
    with app.app_context():
        document = CatalogDocument(
            content_sources=[SourceRef(slug="my-mod", name="My Mod", kind="workshop_mod")],
            objects=[
                ObjectRecord(source_slug="my-mod", template_key="X", type_slug="not_a_real_type", name="X")
            ],
        )

        report = validate_document(document)

        assert not report.is_valid


def test_validate_document_rejects_duplicate_object_in_document(app, seeded_db):
    with app.app_context():
        document = CatalogDocument(
            content_sources=[SourceRef(slug="my-mod", name="My Mod", kind="workshop_mod")],
            objects=[
                ObjectRecord(source_slug="my-mod", template_key="X", type_slug="weapon", name="X"),
                ObjectRecord(source_slug="my-mod", template_key="X", type_slug="weapon", name="X duplicate"),
            ],
        )

        report = validate_document(document)

        assert not report.is_valid


def test_validate_document_flags_existing_object_as_conflict(app, seeded_db):
    with app.app_context():
        from app.models import CatalogObjectType
        from app.services import catalog_objects as objects_service
        from app.services import content_sources as sources_service

        source = sources_service.create_content_source(name="Existing Mod", kind="workshop_mod", slug="existing-mod")
        obj_type = CatalogObjectType.query.filter_by(slug="weapon").first()
        objects_service.create_object(
            source_id=source.id, type_id=obj_type.id, name="Already Here", template_key="AlreadyHere"
        )

        document = CatalogDocument(
            content_sources=[SourceRef(slug="existing-mod", name="Existing Mod", kind="workshop_mod")],
            objects=[
                ObjectRecord(
                    source_slug="existing-mod", template_key="AlreadyHere", type_slug="weapon", name="Already Here"
                )
            ],
        )

        report = validate_document(document)

        assert report.is_valid  # a conflict is a warning, not an error
        assert report.conflicting_objects == 1
        assert report.new_objects == 0


# ---- Import commit + round trip ----


def test_import_document_creates_sources_objects_and_relationships(app, seeded_db):
    with app.app_context():
        from app.models import CatalogObject, CatalogRelationship, ContentSource

        document = _sample_document()
        result = catalog_import.import_document(document)

        assert result.sources_created == 1
        assert result.objects_created == 2
        assert result.relationships_created == 1

        assert ContentSource.query.filter_by(slug="my-mod").first() is not None
        assert CatalogObject.query.filter_by(template_key="Rifle1").first() is not None
        assert CatalogRelationship.query.count() == 1


def test_import_document_raises_for_invalid_document(app, seeded_db):
    import pytest

    with app.app_context():
        document = CatalogDocument(
            content_sources=[SourceRef(slug="my-mod", name="My Mod", kind="workshop_mod")],
            objects=[
                ObjectRecord(source_slug="my-mod", template_key="X", type_slug="weapon", name="X"),
                ObjectRecord(source_slug="my-mod", template_key="X", type_slug="weapon", name="X dup"),
            ],
        )
        with pytest.raises(catalog_import.CatalogImportError):
            catalog_import.import_document(document)


def test_reimporting_same_document_does_not_duplicate(app, seeded_db):
    with app.app_context():
        from app.models import CatalogObject

        document = _sample_document()
        catalog_import.import_document(document)
        count_after_first = CatalogObject.query.count()

        second_result = catalog_import.import_document(document)
        count_after_second = CatalogObject.query.count()

        assert count_after_first == count_after_second
        assert second_result.objects_created == 0
        assert second_result.objects_skipped == 2


def test_export_then_reimport_round_trip_is_idempotent(app, seeded_db):
    with app.app_context():
        from app.models import CatalogObject
        from app.models import ContentSource as ContentSourceModel

        document = _sample_document()
        catalog_import.import_document(document)
        my_mod = ContentSourceModel.query.filter_by(slug="my-mod").first()

        exported = catalog_import.export_document(source_ids=[my_mod.id])
        count_before_reimport = CatalogObject.query.count()

        result = catalog_import.import_document(exported)

        count_after_reimport = CatalogObject.query.count()
        assert count_before_reimport == count_after_reimport
        assert result.objects_created == 0  # everything already existed
        assert result.objects_skipped == len(exported.objects)


def test_export_document_excludes_other_sources_when_filtered(app, seeded_db):
    with app.app_context():
        from app.models import ContentSource as ContentSourceModel

        catalog_import.import_document(_sample_document())
        my_mod = ContentSourceModel.query.filter_by(slug="my-mod").first()

        exported = catalog_import.export_document(source_ids=[my_mod.id])

        assert all(obj.source_slug == "my-mod" for obj in exported.objects)
        assert exported.content_sources[0].slug == "my-mod"
