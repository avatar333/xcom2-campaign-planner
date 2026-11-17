"""Route-level tests for the catalog/content-sources UI (Phase 3 acceptance
criteria 3-9: browse/filter/search, create custom object, image, relationship,
export/import)."""
from __future__ import annotations

import io

from app.models import CatalogObjectType, ContentSource
from tests.helpers import make_png_bytes


def _user_source_id(app) -> int:
    with app.app_context():
        return ContentSource.query.filter_by(slug="user-local").first().id


def _weapon_type_id(app) -> int:
    with app.app_context():
        return CatalogObjectType.query.filter_by(slug="weapon").first().id


def test_catalog_index_renders_empty_state(client, seeded_db):
    response = client.get("/catalog/")
    assert response.status_code == 200
    assert b"No objects found" in response.data


def test_content_sources_list_route(client, seeded_db):
    response = client.get("/content/sources/")
    assert response.status_code == 200
    assert b"XCOM 2" in response.data
    assert b"War of the Chosen" in response.data


def test_create_content_source_via_route(client, app, seeded_db):
    response = client.post(
        "/content/sources/new",
        data={"name": "[WOTC] Some Weapon Pack", "kind": "workshop_mod", "version": "1.0.0"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Some Weapon Pack" in response.data

    with app.app_context():
        source = ContentSource.query.filter_by(name="[WOTC] Some Weapon Pack").first()
        assert source is not None
        assert source.kind == "workshop_mod"


def test_create_catalog_object_via_route(client, app, seeded_db):
    user_source_id = _user_source_id(app)
    weapon_type_id = _weapon_type_id(app)

    response = client.post(
        "/catalog/objects/new",
        data={
            "source_id": user_source_id,
            "type_id": weapon_type_id,
            "name": "Example Rifle",
            "template_key": "",
            "tier": "2",
            "tags": "primary, mod",
            "short_description": "A community mod weapon",
            "long_description": "",
            "attributes_json": "",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Example Rifle" in response.data


def test_catalog_search_filters_by_text(client, app, seeded_db):
    user_source_id = _user_source_id(app)
    weapon_type_id = _weapon_type_id(app)
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "Findable Rifle"},
    )
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "Other Weapon"},
    )

    response = client.get("/catalog/?q=Findable")
    assert b"Findable Rifle" in response.data
    assert b"Other Weapon" not in response.data


def test_catalog_filters_by_type(client, app, seeded_db):
    user_source_id = _user_source_id(app)
    weapon_type_id = _weapon_type_id(app)
    with app.app_context():
        armor_type_id = CatalogObjectType.query.filter_by(slug="armor").first().id

    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "A Weapon"},
    )
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": armor_type_id, "name": "An Armor"},
    )

    response = client.get(f"/catalog/?type_id={weapon_type_id}")
    assert b"A Weapon" in response.data
    assert b"An Armor" not in response.data


def test_object_detail_route(client, app, seeded_db):
    user_source_id = _user_source_id(app)
    weapon_type_id = _weapon_type_id(app)
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "Detail Rifle"},
    )
    with app.app_context():
        from app.models import CatalogObject

        obj = CatalogObject.query.filter_by(name="Detail Rifle").first()
        obj_id = obj.id

    response = client.get(f"/catalog/objects/{obj_id}")
    assert response.status_code == 200
    assert b"Detail Rifle" in response.data


def test_upload_image_via_route(client, app, seeded_db):
    user_source_id = _user_source_id(app)
    weapon_type_id = _weapon_type_id(app)
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "Image Rifle"},
    )
    with app.app_context():
        from app.models import CatalogObject

        obj_id = CatalogObject.query.filter_by(name="Image Rifle").first().id

    response = client.post(
        f"/catalog/objects/{obj_id}/image",
        data={
            "role": "icon",
            "source_url": "",
            "license": "",
            "attribution_text": "",
            "image_file": (io.BytesIO(make_png_bytes()), "test.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        from app.models import CatalogObject

        reloaded = CatalogObject.query.filter_by(name="Image Rifle").first()
        assert reloaded.primary_asset_id is not None

    asset_response = client.get(f"/catalog/assets/{reloaded.primary_asset_id}")
    assert asset_response.status_code == 200
    assert asset_response.mimetype == "image/png"


def test_upload_rejects_non_image(client, app, seeded_db):
    user_source_id = _user_source_id(app)
    weapon_type_id = _weapon_type_id(app)
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "Bad Image Rifle"},
    )
    with app.app_context():
        from app.models import CatalogObject

        obj_id = CatalogObject.query.filter_by(name="Bad Image Rifle").first().id

    response = client.post(
        f"/catalog/objects/{obj_id}/image",
        data={
            "role": "icon",
            "source_url": "",
            "license": "",
            "attribution_text": "",
            "image_file": (io.BytesIO(b"not an image"), "fake.png"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_add_and_remove_relationship_via_route(client, app, seeded_db):
    user_source_id = _user_source_id(app)
    weapon_type_id = _weapon_type_id(app)
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "Rel Object A"},
    )
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "Rel Object B"},
    )
    with app.app_context():
        from app.models import CatalogObject, CatalogRelationshipType

        obj_a = CatalogObject.query.filter_by(name="Rel Object A").first()
        obj_b = CatalogObject.query.filter_by(name="Rel Object B").first()
        rel_type = CatalogRelationshipType.query.filter_by(slug="compatible_with").first()
        obj_a_id, obj_b_id, rel_type_id = obj_a.id, obj_b.id, rel_type.id

    response = client.post(
        f"/catalog/objects/{obj_a_id}/relationships",
        data={
            "relationship_type_id": rel_type_id,
            "direction": "outbound",
            "target_object_id": obj_b_id,
            "group_key": "",
            "amount": "",
            "unit": "",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Rel Object B" in response.data

    with app.app_context():
        from app.models import CatalogRelationship

        rel = CatalogRelationship.query.filter_by(source_object_id=obj_a_id, target_object_id=obj_b_id).first()
        rel_id = rel.id

    delete_response = client.post(f"/catalog/relationships/{rel_id}/delete", follow_redirects=True)
    assert delete_response.status_code == 200

    with app.app_context():
        from app.repositories.catalog_relationships import get_relationship

        assert get_relationship(rel_id) is None


def test_search_json_endpoint_returns_image_and_name(client, app, seeded_db):
    user_source_id = _user_source_id(app)
    weapon_type_id = _weapon_type_id(app)
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "Pickable Object"},
    )

    response = client.get("/catalog/search.json?q=Pickable")
    assert response.status_code == 200
    data = response.get_json()
    assert any(r["name"] == "Pickable Object" for r in data["results"])


def test_overlay_creation_via_route(client, app, seeded_db):
    with app.app_context():
        from app.models import ContentSource

        base_source = ContentSource.query.filter_by(slug="xcom2-base").first()
        weapon_type_id = _weapon_type_id(app)
        from app.services import catalog_objects as objects_service

        base_obj = objects_service.create_object(
            source_id=base_source.id, type_id=weapon_type_id, name="Built-in Rifle"
        )
        base_obj_id = base_obj.id

    response = client.post(f"/catalog/objects/{base_obj_id}/overlay", follow_redirects=True)
    assert response.status_code == 200
    assert b"overlay" in response.data.lower() or b"Built-in Rifle (edited)" in response.data

    with app.app_context():
        from app.models import CatalogObject

        overlay = CatalogObject.query.filter_by(overrides_object_id=base_obj_id).first()
        assert overlay is not None
        assert overlay.is_user_authored is True


def test_export_import_routes_round_trip(client, app, seeded_db):
    user_source_id = _user_source_id(app)
    weapon_type_id = _weapon_type_id(app)
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "Exportable Rifle"},
    )

    export_response = client.get("/catalog/export")
    assert export_response.status_code == 200
    document_text = export_response.data.decode("utf-8")
    assert "Exportable Rifle" in document_text

    with app.app_context():
        from app.models import CatalogObject

        count_before = CatalogObject.query.count()

    import_response = client.post(
        "/catalog/import", data={"document_text": document_text, "confirm": "y"}
    )
    assert import_response.status_code == 200

    with app.app_context():
        from app.models import CatalogObject

        count_after = CatalogObject.query.count()
    assert count_before == count_after  # re-import of an export must not duplicate


def test_content_source_detail_route(client, seeded_db):
    with client.application.app_context():
        from app.models import ContentSource

        source = ContentSource.query.filter_by(slug="xcom2-base").first()
        source_id = source.id

    response = client.get(f"/content/sources/{source_id}")
    assert response.status_code == 200
    assert b"XCOM 2" in response.data


def test_content_source_detail_404(client, seeded_db):
    response = client.get("/content/sources/999999")
    assert response.status_code == 404


def test_content_source_edit_route(client, app, seeded_db):
    with app.app_context():
        from app.services import content_sources as sources_service

        source = sources_service.create_content_source(name="Editable Source", kind="workshop_mod")
        source_id = source.id

    get_response = client.get(f"/content/sources/{source_id}/edit")
    assert get_response.status_code == 200

    post_response = client.post(
        f"/content/sources/{source_id}/edit",
        data={"name": "Renamed Source", "kind": "user", "version": "2.0.0"},
        follow_redirects=True,
    )
    assert post_response.status_code == 200
    assert b"Renamed Source" in post_response.data


def test_content_source_delete_route_success(client, app, seeded_db):
    with app.app_context():
        from app.services import content_sources as sources_service

        source = sources_service.create_content_source(name="Deletable Source", kind="workshop_mod")
        source_id = source.id

    response = client.post(f"/content/sources/{source_id}/delete", follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        from app.repositories import content_sources as sources_repo

        assert sources_repo.get_content_source(source_id) is None


def test_content_source_delete_route_blocked_for_builtin(client, app, seeded_db):
    with app.app_context():
        from app.models import ContentSource

        source_id = ContentSource.query.filter_by(slug="xcom2-base").first().id

    response = client.post(f"/content/sources/{source_id}/delete")
    assert response.status_code == 400


def test_campaign_manage_sources_route(client, app, seeded_db):
    with app.app_context():
        from app.services import campaign_lifecycle

        campaign = campaign_lifecycle.create_campaign(name="Sources Campaign", game_profile_slug="wotc-standard")
        campaign_id = campaign.id

    response = client.get(f"/campaigns/{campaign_id}/sources")
    assert response.status_code == 200
    assert b"XCOM 2" in response.data


def test_campaign_add_remove_toggle_source_routes(client, app, seeded_db):
    with app.app_context():
        from app.models import ContentSource
        from app.services import campaign_lifecycle

        campaign = campaign_lifecycle.create_campaign(name="Toggle Campaign", game_profile_slug=None)
        campaign_id = campaign.id
        source_id = ContentSource.query.filter_by(slug="lwotc").first().id

    add_response = client.post(
        f"/campaigns/{campaign_id}/sources/add", data={"content_source_id": source_id}, follow_redirects=True
    )
    assert add_response.status_code == 200

    toggle_response = client.post(f"/campaigns/{campaign_id}/sources/{source_id}/toggle", follow_redirects=True)
    assert toggle_response.status_code == 200

    remove_response = client.post(f"/campaigns/{campaign_id}/sources/{source_id}/remove", follow_redirects=True)
    assert remove_response.status_code == 200

    with app.app_context():
        from app.repositories import campaign_sources as campaign_sources_repo

        assert campaign_sources_repo.get_association(campaign_id, source_id) is None


def test_campaign_reorder_sources_route(client, app, seeded_db):
    with app.app_context():
        from app.models import ContentSource
        from app.services import campaign_lifecycle

        campaign = campaign_lifecycle.create_campaign(name="Reorder Campaign", game_profile_slug="wotc-standard")
        campaign_id = campaign.id
        base_id = ContentSource.query.filter_by(slug="xcom2-base").first().id
        wotc_id = ContentSource.query.filter_by(slug="wotc").first().id

    response = client.post(
        f"/campaigns/{campaign_id}/sources/reorder",
        data={"content_source_id": [str(wotc_id), str(base_id)]},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        from app.repositories import campaign_sources as campaign_sources_repo

        wotc_assoc = campaign_sources_repo.get_association(campaign_id, wotc_id)
        base_assoc = campaign_sources_repo.get_association(campaign_id, base_id)
        assert wotc_assoc.precedence < base_assoc.precedence


def test_edit_catalog_object_via_route(client, app, seeded_db):
    user_source_id = _user_source_id(app)
    weapon_type_id = _weapon_type_id(app)
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "Editable Object"},
    )
    with app.app_context():
        from app.models import CatalogObject

        obj_id = CatalogObject.query.filter_by(name="Editable Object").first().id

    get_response = client.get(f"/catalog/objects/{obj_id}/edit")
    assert get_response.status_code == 200

    post_response = client.post(
        f"/catalog/objects/{obj_id}/edit",
        data={
            "source_id": user_source_id, "type_id": weapon_type_id, "name": "Edited Object Name",
            "tier": "", "tags": "", "short_description": "", "long_description": "", "attributes_json": "",
        },
        follow_redirects=True,
    )
    assert post_response.status_code == 200
    assert b"Edited Object Name" in post_response.data


def test_delete_catalog_object_via_route(client, app, seeded_db):
    user_source_id = _user_source_id(app)
    weapon_type_id = _weapon_type_id(app)
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "Deletable Object"},
    )
    with app.app_context():
        from app.models import CatalogObject

        obj_id = CatalogObject.query.filter_by(name="Deletable Object").first().id

    response = client.post(f"/catalog/objects/{obj_id}/delete", follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        from app.repositories import catalog_objects as objects_repo

        assert objects_repo.get_object(obj_id) is None


def test_catalog_list_view_mode(client, app, seeded_db):
    user_source_id = _user_source_id(app)
    weapon_type_id = _weapon_type_id(app)
    client.post(
        "/catalog/objects/new",
        data={"source_id": user_source_id, "type_id": weapon_type_id, "name": "List View Object"},
    )
    response = client.get("/catalog/?view=list")
    assert response.status_code == 200
    assert b"List View Object" in response.data


def test_import_catalog_shows_validation_report_before_confirm(client, seeded_db):
    document_text = (
        '{"format": "xcom2-campaign-planner.catalog-export", "version": 1, '
        '"content_sources": [{"slug": "preview-mod", "name": "Preview Mod", "kind": "workshop_mod"}], '
        '"object_types": [], '
        '"objects": [{"source_slug": "preview-mod", "template_key": "T1", "type_slug": "weapon", "name": "Preview Weapon"}], '
        '"relationships": []}'
    )
    response = client.post("/catalog/import", data={"document_text": document_text})
    assert response.status_code == 200
    assert b"Validation Report" in response.data
    assert b"valid and ready to import" in response.data.lower() or b"New objects" in response.data


def test_import_catalog_rejects_malformed_json(client, seeded_db):
    response = client.post("/catalog/import", data={"document_text": "not json {{"})
    assert response.status_code == 200
    assert b"not valid json" in response.data.lower() or b"error" in response.data.lower()
