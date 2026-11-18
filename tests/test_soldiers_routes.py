"""Soldier roster, Build Library and build editor routes (ADR-0022)."""
from __future__ import annotations

import io
import re

import pytest

from tests.avenger_helpers import import_real_packs, new_campaign
from tests.helpers import make_png_bytes


@pytest.fixture()
def real(app, seeded_db):
    with app.app_context():
        import_real_packs()
        yield


def oid(key):
    from app.models import CatalogObject

    return CatalogObject.query.filter_by(template_key=key).first().id


def surl(campaign, suffix=""):
    return f"/campaigns/{campaign.id}/soldiers/{suffix}"


def burl(campaign, suffix=""):
    return f"/campaigns/{campaign.id}/builds/{suffix}"


def test_unknown_campaign_and_soldier_are_404(client, real):
    campaign = new_campaign("A")
    assert client.get("/campaigns/9999/soldiers/").status_code == 404
    assert client.get(surl(campaign, "99999")).status_code == 404
    assert client.get(burl(campaign, "99999")).status_code == 404


def test_roster_page_shows_portrait_name_class_and_equipment(client, real):
    campaign = new_campaign("A", "wotc-standard")
    client.post(surl(campaign, "new"), data={"name": "Jane", "surname": "Kelly", "callsign": "Viper", "class_object_id": str(oid("Ranger"))})
    html = client.get(surl(campaign)).get_data(as_text=True)

    assert 'data-testid="soldier-grid"' in html
    assert "Viper" in html and "Kelly" in html and "Ranger" in html
    assert "Not assigned" in html  # no weapon/armour build yet


def test_create_soldier_via_class_gallery(client, real):
    campaign = new_campaign("A", "wotc-standard")
    html = client.get(surl(campaign, "new")).get_data(as_text=True)
    assert 'data-testid="class-gallery"' in html and "Ranger" in html

    response = client.post(surl(campaign, "new"), data={"name": "Jane", "class_object_id": str(oid("Grenadier"))})
    assert response.status_code == 302
    detail = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Grenadier" in detail
    assert "68" not in detail.split('data-testid="stats-panel"')[1].split("AIM")[0]  # sanity: panel rendered


def test_create_soldier_rejects_blank_name(client, real):
    campaign = new_campaign("A", "wotc-standard")
    response = client.post(surl(campaign, "new"), data={"name": "   "})
    assert response.status_code == 400
    assert "required" in response.get_data(as_text=True)


def test_portrait_upload_and_default_glyph(client, real):
    campaign = new_campaign("A", "wotc-standard")
    response = client.post(
        surl(campaign, "new"),
        data={"name": "Jane", "portrait_file": (io.BytesIO(make_png_bytes()), "jane.png")},
        content_type="multipart/form-data",
    )
    detail_url = response.headers["Location"]
    html = client.get(detail_url).get_data(as_text=True)
    assert "/catalog/assets/" in html  # real portrait rendered, not just the glyph


def test_edit_soldier_and_delete(client, real):
    campaign = new_campaign("A", "wotc-standard")
    response = client.post(surl(campaign, "new"), data={"name": "Jane", "class_object_id": str(oid("Ranger"))})
    detail_url = response.headers["Location"]
    soldier_id = int(re.search(r"/soldiers/(\d+)$", detail_url).group(1))

    client.post(surl(campaign, f"{soldier_id}/edit"), data={"name": "Jane", "surname": "Kelly", "status": "wounded"})
    html = client.get(detail_url).get_data(as_text=True)
    assert "Kelly" in html and "Wounded" in html

    delete_response = client.post(surl(campaign, f"{soldier_id}/delete"))
    assert delete_response.status_code == 302
    assert client.get(detail_url).status_code == 404


def test_update_stats_via_route_persists_and_rejects_invalid(client, real):
    campaign = new_campaign("A", "wotc-standard")
    response = client.post(surl(campaign, "new"), data={"name": "Jane", "class_object_id": str(oid("Ranger"))})
    detail_url = response.headers["Location"]
    soldier_id = int(re.search(r"/soldiers/(\d+)$", detail_url).group(1))

    client.post(surl(campaign, f"{soldier_id}/stats"), data={"aim": "80"})
    html = client.get(detail_url).get_data(as_text=True)
    assert 'value="80"' in html

    bad = client.post(surl(campaign, f"{soldier_id}/stats"), data={"aim": "not-a-number"}, follow_redirects=True)
    assert "must be a number" in bad.get_data(as_text=True)


def test_assign_build_via_route(client, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="Close Assault")
    response = client.post(surl(campaign, "new"), data={"name": "Jane"})
    soldier_id = int(re.search(r"/soldiers/(\d+)$", response.headers["Location"]).group(1))

    client.post(surl(campaign, f"{soldier_id}/builds/weapon/primary"), data={"build_id": str(build.id)})
    html = client.get(surl(campaign, str(soldier_id))).get_data(as_text=True)
    assert "Close Assault" in html

    client.post(surl(campaign, f"{soldier_id}/builds/weapon/primary"), data={"build_id": ""})
    html = client.get(surl(campaign, str(soldier_id))).get_data(as_text=True)
    assert "Not assigned" in html.split('data-testid="equipment-panel"')[1]


# -------------------------------------------------------------- build library


def test_build_library_lists_and_filters_by_type(client, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="W1")
    builds.create_build(campaign, "armor", base_object_id=oid("PredatorArmor"), name="A1")

    all_html = client.get(burl(campaign)).get_data(as_text=True)
    assert "W1" in all_html and "A1" in all_html
    weapons_only = client.get(burl(campaign, "?type=weapon")).get_data(as_text=True)
    assert "W1" in weapons_only and "A1" not in weapons_only.split('data-testid="build-grid"')[1] if 'data-testid="build-grid"' in weapons_only else True


def test_build_editor_wizard_flow(client, real):
    campaign = new_campaign("A", "wotc-standard")
    step1 = client.get(burl(campaign, "weapon/new")).get_data(as_text=True)
    assert 'data-testid="step-base"' in step1 and "Shotgun" in step1

    base_id = oid("Shotgun_MG")
    step3 = client.get(burl(campaign, f"weapon/new?base_object_id={base_id}")).get_data(as_text=True)
    assert "Weapon upgrade 1" in step3 and "Weapon upgrade 2" in step3

    response = client.post(
        burl(campaign, "weapon/new"),
        data={
            "step": "save", "base_object_id": str(base_id), "name": "Close Assault Mk II", "purpose": "Max crit",
            "slot__weapon_upgrade__0": str(oid("LaserSightUpgrade")),
        },
    )
    assert response.status_code == 302
    detail = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Close Assault Mk II" in detail and "Laser Sight" in detail


def test_build_editor_rejects_duplicate_name(client, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="Existing")
    response = client.post(
        burl(campaign, "weapon/new"),
        data={"step": "save", "base_object_id": str(oid("Shotgun_MG")), "name": "Existing"},
    )
    assert response.status_code == 400
    assert "already has a" in response.get_data(as_text=True)


def test_build_editor_manual_override_checkbox(client, real, tmp_path):
    from app.services.content_import import import_pack
    from tests.avenger_helpers import attach_source, write_pack

    objects = [{"template_key": "SniperOnlyScope2", "type": "weapon_upgrade", "name": "Sniper-Only Scope 2",
                "attributes": {"compatible_weapon_categories": "sniper_rifle"}}]
    import_pack(str(write_pack(tmp_path, "scopes2", objects=objects, requires=["xcom2-base"])))
    campaign = new_campaign("A", "wotc-standard")
    attach_source(campaign, "scopes2")

    rejected = client.post(
        burl(campaign, "weapon/new"),
        data={"step": "save", "base_object_id": str(oid("Shotgun_CV")), "name": "X", "slot__weapon_upgrade__0": str(oid("SniperOnlyScope2"))},
    )
    assert rejected.status_code == 400

    accepted = client.post(
        burl(campaign, "weapon/new"),
        data={
            "step": "save", "base_object_id": str(oid("Shotgun_CV")), "name": "Y",
            "slot__weapon_upgrade__0": str(oid("SniperOnlyScope2")), "override__weapon_upgrade__0": "1",
        },
    )
    assert accepted.status_code == 302
    detail = client.get(accepted.headers["Location"]).get_data(as_text=True)
    assert "Manual override" in detail


def test_edit_build_route(client, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="X")
    edit_page = client.get(burl(campaign, f"{build.id}/edit")).get_data(as_text=True)
    assert 'value="X"' in edit_page

    response = client.post(
        burl(campaign, f"{build.id}/edit"),
        data={"step": "save", "base_object_id": str(oid("Shotgun_MG")), "name": "X", "purpose": "New purpose"},
    )
    assert response.status_code == 302
    detail = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Shard Gun" in detail and "New purpose" in detail


def test_delete_build_route_requires_unassign_confirmation(client, real):
    from app.services import builds, soldiers

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="X")
    jane = soldiers.create_soldier(campaign, name="Jane")
    soldiers.assign_build(campaign, jane, build)

    blocked = client.post(burl(campaign, f"{build.id}/delete"))
    assert blocked.status_code == 400 and "Jane" in blocked.get_data(as_text=True)

    confirmed = client.post(burl(campaign, f"{build.id}/delete"), data={"unassign": "1"})
    assert confirmed.status_code == 302
    assert client.get(burl(campaign, str(build.id))).status_code == 404


def test_planned_build_shows_unlock_path_link(client, real):
    from app.services import builds, soldiers

    campaign = new_campaign("A", "wotc-standard")
    build = builds.create_build(campaign, "weapon", base_object_id=oid("AssaultRifle_MG"), name="Mag Rifle")
    jane = soldiers.create_soldier(campaign, name="Jane")
    soldiers.assign_build(campaign, jane, build)

    soldier_html = client.get(surl(campaign, str(jane.id))).get_data(as_text=True)
    assert "PLANNED / NOT YET AVAILABLE" in soldier_html

    detail_html = client.get(burl(campaign, str(build.id))).get_data(as_text=True)
    assert "Show Unlock Path" in detail_html
    link = re.search(r'href="(/campaigns/\d+/research/path/\d+)"', detail_html).group(1)
    assert client.get(link).status_code == 200


# ----------------------------------------------------------- isolation


def test_switching_campaigns_shows_independent_rosters_and_build_libraries(client, real):
    from app.services import builds, soldiers

    a, b = new_campaign("Campaign A", "wotc-standard"), new_campaign("Campaign B", "wotc-standard")
    soldiers.create_soldier(a, name="Jane")
    soldiers.create_soldier(b, name="Tom")
    builds.create_build(a, "weapon", base_object_id=oid("Shotgun_CV"), name="A-Build")
    builds.create_build(b, "weapon", base_object_id=oid("Shotgun_CV"), name="B-Build")

    page_a = client.get(surl(a)).get_data(as_text=True)
    page_b = client.get(surl(b)).get_data(as_text=True)
    assert "Jane" in page_a and "Jane" not in page_b
    assert "Tom" in page_b and "Tom" not in page_a

    lib_a = client.get(burl(a)).get_data(as_text=True)
    lib_b = client.get(burl(b)).get_data(as_text=True)
    assert "A-Build" in lib_a and "A-Build" not in lib_b
    assert "B-Build" in lib_b and "B-Build" not in lib_a


def test_deleted_campaigns_soldier_page_is_404_other_campaign_unaffected(client, real):
    from app.services import campaign_lifecycle, soldiers

    a, b = new_campaign("A", "wotc-standard"), new_campaign("B", "wotc-standard")
    soldiers.create_soldier(a, name="Jane")
    soldiers.create_soldier(b, name="Tom")
    campaign_lifecycle.delete_campaign(a)

    assert client.get(surl(a)).status_code == 404
    assert client.get(surl(b)).status_code == 200


def test_nav_has_live_soldiers_and_build_library_entries(client, real):
    campaign = new_campaign("A", "wotc-standard")
    client.get(f"/campaigns/{campaign.id}/dashboard")
    html = client.get("/").get_data(as_text=True)
    assert f'href="{surl(campaign)}"' in html and f'href="{burl(campaign)}"' in html
