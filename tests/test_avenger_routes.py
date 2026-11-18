"""Avenger planner routes and rendered UI (ADR-0020, UI_UX_SPEC §3.10)."""
from __future__ import annotations

import re

import pytest

from tests.avenger_helpers import facility_id, import_real_packs, new_campaign, room_id

HTMX = {"HX-Request": "true"}


@pytest.fixture()
def real(app, seeded_db):
    with app.app_context():
        import_real_packs()
        yield


def _url(campaign, suffix=""):
    return f"/campaigns/{campaign.id}/avenger/{suffix}"


def _coordinates_in(html: str) -> list[str]:
    return re.findall(r'class="av-cell[^"]*"[^>]*data-coordinate="(R\dC\d)"', html)


def test_unknown_campaign_is_404(client, real):
    assert client.get("/campaigns/9999/avenger/").status_code == 404


def test_page_shows_twelve_cells_in_three_by_four_order(client, real):
    campaign = new_campaign("A")
    response = client.get(_url(campaign))
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert _coordinates_in(html) == [f"R{r}C{c}" for r in range(1, 5) for c in range(1, 4)]
    assert "3 columns by 4 rows" in html
    assert "grid-template-columns: repeat(3" not in html  # layout lives in CSS, not inline tables
    assert "<table" not in html.split('data-testid="avenger-grid"')[1].split("</section>")[0]


def test_unconfigured_campaign_shows_empty_placeholders_and_banner(client, real):
    campaign = new_campaign("A")
    html = client.get(_url(campaign)).get_data(as_text=True)

    assert 'data-testid="not-configured-banner"' in html
    assert "Layout not configured yet" in html
    assert html.count("EMPTY (unconfigured)") == 12
    assert html.count("is-unset") >= 12
    assert "Configure Starting Avenger" in html


def test_configure_page_lists_catalog_room_states(client, real):
    campaign = new_campaign("A")
    html = client.get(_url(campaign, "?mode=configure")).get_data(as_text=True)

    assert 'data-testid="configure-form"' in html
    assert 'id="av-layout-data"' in html
    for name in ("Alien Debris", "Alien Machinery", "Empty / Cleared", "Exposed Power Coil"):
        assert name in html
    assert "Save Layout" in html and "Cancel" in html and "Reset to EMPTY" in html


def test_save_layout_persists_and_survives_reload(app, client, real):
    from app.services import avenger

    campaign = new_campaign("A")
    data = {f"room_R{r}C{c}": str(room_id(campaign, "Empty / Cleared")) for r in range(1, 5) for c in range(1, 4)}
    data["room_R2C3"] = str(room_id(campaign, "Alien Debris"))
    data["room_R4C1"] = str(room_id(campaign, "Exposed Power Coil"))

    response = client.post(_url(campaign, "configure"), data=data)
    assert response.status_code == 302

    with app.test_client() as fresh:  # a brand new client == a reloaded application session
        html = fresh.get(_url(campaign)).get_data(as_text=True)
    assert "not-configured-banner" not in html
    assert "Alien Debris" in html and "Exposed Power Coil" in html
    view = avenger.get_grid_view(campaign)
    assert view.is_fully_configured and view.cell("R2C3").room.name == "Alien Debris"


def test_save_layout_with_invalid_entry_changes_nothing(client, real):
    from app.services import avenger

    campaign = new_campaign("A")
    bad = {"room_R1C1": str(room_id(campaign, "Alien Debris")), "room_R1C2": str(facility_id(campaign, "Laboratory"))}
    response = client.post(_url(campaign, "configure"), data=bad)

    assert response.status_code == 400
    assert avenger.get_grid_view(campaign).summary.configured_cells == 0
    assert client.post(_url(campaign, "configure"), data={"room_R1C1": "abc"}).status_code == 400


def test_cancel_is_a_plain_link_that_saves_nothing(client, real):
    from app.services import avenger

    campaign = new_campaign("A")
    html = client.get(_url(campaign, "?mode=configure")).get_data(as_text=True)
    assert f'href="{_url(campaign)}"' in html  # Cancel navigates away; only POST /configure persists
    assert avenger.get_grid_view(campaign).summary.configured_cells == 0


def test_reset_route_sets_all_cells_to_empty(client, real):
    from app.services import avenger

    campaign = new_campaign("A")
    client.post(_url(campaign, "reset"))

    view = avenger.get_grid_view(campaign)
    assert all(c.room.name == "Empty / Cleared" for c in view.cells)


def test_cell_modal_fragment_lists_catalog_facilities_as_cards(client, real):
    campaign = new_campaign("A", "wotc-standard")
    response = client.get(_url(campaign, "cells/R4C2"), headers=HTMX)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-testid="cell-modal"' in html
    for section in ("Cell", "Current", "Plan", "Available facilities"):
        assert section in html
    assert html.count("av-fcard__name") == 12
    for name in ("Laboratory", "Power Relay", "Infirmary", "Resistance Ring"):
        assert name in html
    card_names = re.findall(r'av-fcard__name">([^<]+)<', html)
    assert "Advanced Warfare Center" not in card_names  # ruleset-filtered (removed in WotC)


def test_cell_modal_without_htmx_redirects_to_page_with_modal(client, real):
    campaign = new_campaign("A")
    response = client.get(_url(campaign, "cells/r3c1"))
    assert response.status_code == 302 and response.headers["Location"].endswith("?cell=R3C1")

    page = client.get(_url(campaign, "?cell=R3C1")).get_data(as_text=True)
    assert 'data-testid="cell-modal"' in page and "Cell R3C1" in page


def test_invalid_coordinates_are_404(client, real):
    campaign = new_campaign("A")
    assert client.get(_url(campaign, "cells/R9C9"), headers=HTMX).status_code == 404
    assert client.post(_url(campaign, "cells/R5C1/facility"), data={"facility_id": "1"}).status_code == 404
    assert client.post(_url(campaign, "cells/X/clear")).status_code == 404


def test_assign_facility_and_see_it_in_the_grid(client, real):
    campaign = new_campaign("A")
    response = client.post(
        _url(campaign, "cells/R2C2/facility"),
        data={"facility_id": str(facility_id(campaign, "Proving Ground")), "build_state": "planned"},
    )
    assert response.status_code == 302

    html = client.get(_url(campaign)).get_data(as_text=True)
    tile = re.search(r'<a class="av-cell[^"]*"[^>]*data-coordinate="R2C2".*?</a>', html, re.DOTALL).group(0)
    assert "Proving Ground" in tile
    assert "av-cell--planned_blocked" in tile
    assert "av-cell__warn" in tile
    assert "ADVENT Officer Autopsy" in html  # surfaced in the unresolved-prerequisites panel
    assert 'data-stat="planned">1<' in html and 'data-stat="blocked">1<' in html


def test_htmx_success_uses_hx_redirect(client, real):
    campaign = new_campaign("A")
    response = client.post(
        _url(campaign, "cells/R1C1/facility"),
        data={"facility_id": str(facility_id(campaign, "Workshop")), "build_state": "built"},
        headers=HTMX,
    )
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == _url(campaign)


def test_duplicate_assignment_is_rejected_with_a_message(client, real):
    campaign = new_campaign("A")
    lab = str(facility_id(campaign, "Laboratory"))
    client.post(_url(campaign, "cells/R1C1/facility"), data={"facility_id": lab, "build_state": "planned"})

    response = client.post(_url(campaign, "cells/R1C2/facility"), data={"facility_id": lab, "build_state": "planned"})
    assert response.status_code == 400
    assert "can only be built once" in response.get_data(as_text=True)

    htmx_response = client.post(
        _url(campaign, "cells/R1C2/facility"), data={"facility_id": lab, "build_state": "planned"}, headers=HTMX
    )
    assert htmx_response.status_code == 200
    assert htmx_response.headers["HX-Retarget"] == "[data-modal-root]"
    assert "can only be built once" in htmx_response.get_data(as_text=True)


def test_invalid_and_missing_facility_ids_are_rejected(client, real):
    campaign = new_campaign("A")
    assert client.post(_url(campaign, "cells/R1C1/facility"), data={"facility_id": "99999"}).status_code == 400
    assert client.post(_url(campaign, "cells/R1C1/facility"), data={}).status_code == 400
    assert client.post(
        _url(campaign, "cells/R1C1/facility"),
        data={"facility_id": str(facility_id(campaign, "Workshop")), "build_state": "teleported"},
    ).status_code == 400


def test_build_state_clear_and_room_routes(client, real):
    from app.services import avenger

    campaign = new_campaign("A")
    client.post(_url(campaign, "cells/R1C1/facility"), data={"facility_id": str(facility_id(campaign, "Workshop"))})

    client.post(_url(campaign, "cells/R1C1/state"), data={"build_state": "constructing"})
    assert avenger.get_cell_view(campaign, "R1C1").build_state == "constructing"
    html = client.get(_url(campaign)).get_data(as_text=True)
    assert "av-cell--constructing" in html

    client.post(_url(campaign, "cells/R1C1/room"), data={"room_state_id": str(room_id(campaign, "Alien Debris"))})
    assert avenger.get_cell_view(campaign, "R1C1").room.name == "Alien Debris"
    assert client.post(_url(campaign, "cells/R1C1/room"), data={"room_state_id": "99999"}).status_code == 400

    client.post(_url(campaign, "cells/R1C1/clear"))
    assert avenger.get_cell_view(campaign, "R1C1").facility is None
    assert client.post(_url(campaign, "cells/R1C1/state"), data={"build_state": "built"}).status_code == 400


def test_built_state_is_visually_distinct_from_planned(client, real):
    campaign = new_campaign("A")
    lab, workshop = facility_id(campaign, "Laboratory"), facility_id(campaign, "Workshop")
    client.post(_url(campaign, "cells/R1C1/facility"), data={"facility_id": str(lab), "build_state": "built"})
    client.post(_url(campaign, "cells/R1C2/facility"), data={"facility_id": str(workshop), "build_state": "planned"})
    html = client.get(_url(campaign)).get_data(as_text=True)

    assert 'data-coordinate="R1C1" data-status="built"' in html
    assert re.search(r'data-coordinate="R1C2" data-status="planned_', html)


def test_prerequisite_mark_complete_flow_unblocks_and_keeps_plan(client, real):
    from app.models import CatalogObject

    campaign = new_campaign("A")
    client.post(_url(campaign, "cells/R2C2/facility"), data={"facility_id": str(facility_id(campaign, "Proving Ground"))})
    modal = client.get(_url(campaign, "cells/R2C2"), headers=HTMX).get_data(as_text=True)
    assert 'data-testid="cell-blockers"' in modal and "Mark ADVENT Officer Autopsy complete" in modal

    autopsy = CatalogObject.query.filter_by(template_key="AutopsyAdventOfficer").first()
    response = client.post(_url(campaign, f"prerequisites/{autopsy.id}"), data={"completed": "1", "cell": "R2C2"})
    assert response.status_code == 302 and response.headers["Location"].endswith("?cell=R2C2")

    modal = client.get(_url(campaign, "cells/R2C2"), headers=HTMX).get_data(as_text=True)
    assert 'data-testid="cell-blockers"' not in modal
    assert "Available to build" in modal and "Proving Ground" in modal
    assert client.post(_url(campaign, "prerequisites/99999"), data={"completed": "1"}).status_code == 404


def test_facility_detail_fragment(client, real):
    campaign = new_campaign("A")
    response = client.get(_url(campaign, f"facilities/{facility_id(campaign, 'Proving Ground')}"), headers=HTMX)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "125 supplies" in html and "7 days" in html and "3 power used" in html
    assert "ADVENT Officer Autopsy" in html
    assert "Can only be built once" in html
    assert client.get(_url(campaign, "facilities/99999")).status_code == 404


def test_missing_data_is_rendered_as_not_recorded_never_invented(client, real):
    campaign = new_campaign("A")
    html = client.get(_url(campaign, f"facilities/{facility_id(campaign, 'Shadow Chamber')}")).get_data(as_text=True)
    assert "Not recorded" in html


def test_switching_campaigns_shows_each_campaigns_own_grid(client, real):
    from app.services import avenger

    a, b = new_campaign("Campaign A"), new_campaign("Campaign B")
    avenger.assign_facility(a, "R1C1", facility_id(a, "Laboratory"), build_state="built")
    avenger.assign_facility(b, "R1C1", facility_id(b, "Workshop"), build_state="constructing")

    page_a = client.get(_url(a)).get_data(as_text=True)
    assert "Laboratory" in page_a and "Workshop" not in page_a.split('data-testid="avenger-grid"')[1].split("</section>")[0]
    assert "Campaign A" in page_a

    page_b = client.get(_url(b)).get_data(as_text=True)
    assert "Workshop" in page_b.split('data-testid="avenger-grid"')[1].split("</section>")[0]
    assert "Laboratory" not in page_b.split('data-testid="avenger-grid"')[1].split("</section>")[0]
    assert "Campaign B" in page_b

    with client.session_transaction() as session:
        assert session["active_campaign_id"] == b.id  # the last visited campaign is the active one
    assert client.get(_url(a)).get_data(as_text=True).count("Campaign A") >= 2


def test_nav_links_to_active_campaigns_avenger_page(client, real):
    campaign = new_campaign("Nav campaign")
    client.get(f"/campaigns/{campaign.id}/dashboard")
    html = client.get("/").get_data(as_text=True)
    assert f'href="{_url(campaign)}"' in html


def test_deleted_campaign_page_is_404_but_other_campaign_still_renders(client, real):
    from app.services import campaign_lifecycle

    a, b = new_campaign("A"), new_campaign("B")
    campaign_lifecycle.delete_campaign(a)

    assert client.get(_url(a)).status_code == 404
    assert client.get(_url(b)).status_code == 200


def test_cell_art_uses_facility_image_when_catalog_object_has_one(client, real):
    from app.extensions import db
    from app.models import CatalogObject
    from app.services import asset_service, avenger
    from tests.helpers import make_png_bytes

    campaign = new_campaign("A")
    workshop = CatalogObject.query.filter_by(template_key="Workshop").first()
    asset = asset_service.upload_image_for_object(workshop.id, role="icon", raw_bytes=make_png_bytes())
    db.session.refresh(workshop)
    avenger.assign_facility(campaign, "R1C1", facility_id(campaign, "Workshop"))

    html = client.get(_url(campaign)).get_data(as_text=True)
    tile = re.search(r'<a class="av-cell[^"]*"[^>]*data-coordinate="R1C1".*?</a>', html, re.DOTALL).group(0)
    assert f"/catalog/assets/{workshop.primary_asset_id}" in tile and asset is not None
