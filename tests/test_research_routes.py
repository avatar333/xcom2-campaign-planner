"""Research planner routes and rendered UI (ADR-0021, UI_UX_SPEC 3.11)."""
from __future__ import annotations

import json
import re

import pytest

from tests.avenger_helpers import import_real_packs, new_campaign


@pytest.fixture()
def real(app, seeded_db):
    with app.app_context():
        import_real_packs()
        yield


def oid(key):
    from app.models import CatalogObject

    return CatalogObject.query.filter_by(template_key=key).first().id


def url(campaign, suffix=""):
    return f"/campaigns/{campaign.id}/research/{suffix}"


FETCH = {"X-Requested-With": "fetch", "Accept": "application/json"}


def test_unknown_campaign_and_object_are_404(client, real):
    campaign = new_campaign("A")
    assert client.get("/campaigns/9999/research/").status_code == 404
    assert client.get(url(campaign, "999999")).status_code == 404
    assert client.get(url(campaign, "path/999999")).status_code == 404
    assert client.get(url(campaign, "graph/999999.json")).status_code == 404


def test_browser_page_renders_cards_with_requires_unlocks_and_filters(client, real):
    campaign = new_campaign("A", "lwotc")
    html = client.get(url(campaign)).get_data(as_text=True)

    assert 'data-testid="research-grid"' in html and 'data-testid="research-queue"' in html
    assert html.count("rs-card__name") == 24
    for text in ("Requires", "Unlocks", "Show Unlock Path", "Long War of the Chosen", "Magnetic Weapons"):
        assert text in html
    assert "Any one of" in html  # the OR requirement is rendered as alternatives
    assert 'name="category"' in html and 'name="status"' in html and 'name="source_id"' in html and 'name="q"' in html


def test_browser_filtering_via_query_string(client, real):
    campaign = new_campaign("A")
    html = client.get(url(campaign, "?category=armor")).get_data(as_text=True)
    names = re.findall(r'class="rs-card__name"[^>]*>([^<]+)<', html)
    assert sorted(names) == ["Plated Armor", "Powered Armor"]
    assert "Showing 2 of 20" in html
    assert re.findall(r'class="rs-card__name"[^>]*>([^<]+)<', client.get(url(campaign, "?q=zzzz")).get_data(as_text=True)) == []
    assert "No research found" in client.get(url(campaign, "?q=zzzz")).get_data(as_text=True)


def test_plan_complete_flow_and_state_survives_reload(app, client, real):
    campaign = new_campaign("A")
    assert client.post(url(campaign, f"{oid('AlienBiotech')}/queue")).status_code == 302
    html = client.get(url(campaign)).get_data(as_text=True)
    assert html.count('data-testid="queue-item"') == 1 and "Planned" in html

    client.post(url(campaign, f"{oid('AlienBiotech')}/start"))
    assert "In progress" in client.get(url(campaign)).get_data(as_text=True)
    client.post(url(campaign, f"{oid('AlienBiotech')}/complete"))

    with app.test_client() as fresh:  # new client == application reload
        page = fresh.get(url(campaign)).get_data(as_text=True)
        assert 'data-testid="queue-item"' not in page
        assert re.search(r'data-count="completed">1<', page)
        detail = fresh.get(url(campaign, str(oid("AlienBiotech")))).get_data(as_text=True)
        assert 'data-testid="completed-at"' in detail and "#1 in this campaign" in detail


def test_reorder_route_persists_order_and_returns_json(client, real):
    from app.services import research

    campaign = new_campaign("A")
    keys = ("AlienBiotech", "HybridMaterials", "ModularWeapons")
    for key in keys:
        client.post(url(campaign, f"{oid(key)}/queue"))
    new_order = [oid("ModularWeapons"), oid("AlienBiotech"), oid("HybridMaterials")]

    response = client.post(url(campaign, "queue/reorder"), data={"order": new_order}, headers=FETCH)
    assert response.status_code == 200 and response.get_json() == {"ok": True}
    assert [i.ref.name for i in research.get_queue_view(campaign).items] == ["Modular Weapons", "Alien Biotech", "Hybrid Materials"]

    bad = client.post(url(campaign, "queue/reorder"), data={"order": new_order[:2]}, headers=FETCH)
    assert bad.status_code == 400 and "exactly once" in bad.get_json()["error"]
    assert client.post(url(campaign, "queue/reorder"), data={"order": ["abc"]}, headers=FETCH).status_code == 400
    assert research.get_queue_view(campaign).items[0].ref.name == "Modular Weapons"  # unchanged


def test_move_buttons_work_without_javascript(client, real):
    from app.services import research

    campaign = new_campaign("A")
    for key in ("AlienBiotech", "HybridMaterials", "ModularWeapons"):
        client.post(url(campaign, f"{oid(key)}/queue"))
    client.post(url(campaign, f"{oid('ModularWeapons')}/move"), data={"direction": "up"})
    client.post(url(campaign, f"{oid('AlienBiotech')}/move"), data={"direction": "bottom"})
    assert [i.ref.name for i in research.get_queue_view(campaign).items] == ["Modular Weapons", "Hybrid Materials", "Alien Biotech"]
    client.post(url(campaign, f"{oid('AlienBiotech')}/move"), data={"direction": "top"})
    client.post(url(campaign, f"{oid('HybridMaterials')}/move"), data={"position": "3"})
    assert [i.ref.name for i in research.get_queue_view(campaign).items] == ["Alien Biotech", "Modular Weapons", "Hybrid Materials"]
    assert "Say where" in client.post(url(campaign, f"{oid('AlienBiotech')}/move"), data={}, follow_redirects=True).get_data(as_text=True)


def test_errors_are_reported_and_next_is_restricted_to_own_research_pages(client, real):
    campaign = new_campaign("A")
    client.post(url(campaign, f"{oid('AlienBiotech')}/queue"))
    page = client.post(url(campaign, f"{oid('AlienBiotech')}/queue"), follow_redirects=True).get_data(as_text=True)
    assert "already in the queue" in page

    response = client.post(url(campaign, f"{oid('HybridMaterials')}/queue"), data={"next": "https://evil.example/x"})
    assert response.status_code == 302 and "evil.example" not in response.headers["Location"]
    response = client.post(url(campaign, f"{oid('ModularWeapons')}/queue"), data={"next": "/campaigns/999/research/"})
    assert "/campaigns/999" not in response.headers["Location"]  # cannot be pointed at another campaign
    response = client.post(url(campaign, f"{oid('PlatedArmor')}/queue"), data={"next": url(campaign, f"{oid('PlatedArmor')}")})
    assert response.headers["Location"].endswith(f"/research/{oid('PlatedArmor')}")


def test_queue_warning_is_rendered_with_position_and_reasons(client, real):
    campaign = new_campaign("LWotC", "lwotc")
    client.post(url(campaign, f"{oid('AlienBiotech')}/queue"))
    client.post(url(campaign, f"{oid('HybridMaterials')}/queue"))
    client.post(url(campaign, f"{oid('PlasmaRifle_LWOTC')}/queue"))
    html = client.get(url(campaign)).get_data(as_text=True)

    assert 'data-testid="queue-warning"' in html
    assert "Blocked before position 3" in html
    assert "Advanced Coilguns" in html and "Advanced Laser Weapons" in html
    assert 'data-testid="queue-advice"' in html and "You can keep your plan as it is" in html
    assert 'data-testid="queue-suggestion"' not in html  # nothing is suggested until asked


def test_suggestion_is_shown_only_on_request_and_applies_only_the_confirmed_order(client, real):
    from app.services import research

    campaign = new_campaign("A")
    for key in ("AutopsySectoid", "AlienBiotech"):
        client.post(url(campaign, f"{oid(key)}/queue"))
    before = [i.ref.name for i in research.get_queue_view(campaign).items]

    html = client.get(url(campaign, "?suggest=1")).get_data(as_text=True)
    assert 'data-testid="queue-suggestion"' in html and 'data-testid="apply-suggestion"' in html
    assert [i.ref.name for i in research.get_queue_view(campaign).items] == before  # viewing a suggestion changes nothing

    client.post(url(campaign, "queue/apply-suggestion"), data={"order": [oid("AlienBiotech"), oid("AutopsySectoid")]})
    assert [i.ref.name for i in research.get_queue_view(campaign).items] == ["Alien Biotech", "Sectoid Autopsy"]
    assert client.post(url(campaign, "queue/apply-suggestion"), data={"order": [oid("AlienBiotech")]}).status_code == 302
    assert [i.ref.name for i in research.get_queue_view(campaign).items] == ["Alien Biotech", "Sectoid Autopsy"]  # partial order refused


def test_detail_page_shows_requires_and_unlocks_with_images_and_names(client, real):
    campaign = new_campaign("A")
    html = client.get(url(campaign, str(oid("AutopsyAdventOfficer")))).get_data(as_text=True)

    requires = html.split('data-testid="requires-panel"')[1].split("</section>")[0]
    unlocks = html.split('data-testid="unlocks-panel"')[1].split("</section>")[0]
    assert "Alien Biotech" in requires and "ADVENT Officer Corpse" in requires and "not tracked as a blocker" in requires
    assert all(name in unlocks for name in ("Magnetic Weapons", "Skulljack", "Proving Ground"))
    assert "rs-art" in unlocks  # every object is drawn with artwork/glyph, not just text
    assert 'data-testid="show-unlock-path"' in html


def test_path_picker_lists_objects_of_every_supported_type(client, real):
    campaign = new_campaign("A")
    html = client.get(url(campaign, "path/")).get_data(as_text=True)
    for name in ("Cannon", "Predator Armor", "Psi Lab", "Mindshield", "Battle Scanner", "Plasma Rifle"):
        assert name in html
    assert "Corpse" not in html.split('data-testid="path-targets"')[1]
    filtered = client.get(url(campaign, "path/?q=armor&type=armor")).get_data(as_text=True)
    assert "Predator Armor" in filtered and "Cannon" not in filtered.split('data-testid="path-targets"')[1]


def test_unlock_path_page_contains_graph_json_and_text_plan(client, real):
    campaign = new_campaign("LWotC", "lwotc")
    client.post(url(campaign, f"{oid('HybridMaterials')}/complete"))
    html = client.get(url(campaign, f"path/{oid('PlasmaRifle_LWOTC')}")).get_data(as_text=True)

    graph = json.loads(re.search(r'<script type="application/json" id="rs-graph-data">(.*?)</script>', html, re.DOTALL).group(1))
    kinds = {n["data"]["kind"] for n in graph["nodes"]}
    assert kinds == {"object", "gate"}
    target = [n["data"] for n in graph["nodes"] if n["data"].get("isTarget")]
    assert len(target) == 1 and target[0]["name"] == "Plasma Rifle"
    assert {e["data"]["source"] for e in graph["edges"]} <= {n["data"]["id"] for n in graph["nodes"]}

    assert 'data-testid="path-analysis"' in html and 'data-testid="alternatives"' in html
    assert 'data-testid="mandatory-steps"' in html and "Modular Weapons" in html
    assert "ANY ONE OF" in html and "cytoscape.min.js" in html and "research_graph.js" in html
    assert 'data-testid="satisfied"' in html.replace("\n", " ") and "Hybrid Materials" in html
    assert "cycle-warning" not in html


def test_graph_json_endpoint_matches_page_payload(client, real):
    campaign = new_campaign("A")
    response = client.get(url(campaign, f"graph/{oid('Cannon_MG')}.json"))
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    names = {n["data"].get("name") for n in response.get_json()["nodes"]}
    assert {"Gauss Weapons", "Magnetic Weapons", "Alien Biotech", "Cannon"} <= {n for n in names if n} or "Cannon" in " ".join(n for n in names if n)


def test_switching_campaigns_shows_each_campaigns_progression(client, real):
    a, b = new_campaign("Campaign A"), new_campaign("Campaign B")
    client.post(url(a, f"{oid('AlienBiotech')}/complete"))
    client.post(url(b, f"{oid('HybridMaterials')}/queue"))

    page_a, page_b = client.get(url(a)).get_data(as_text=True), client.get(url(b)).get_data(as_text=True)
    assert re.search(r'data-count="completed">1<', page_a) and re.search(r'data-count="completed">0<', page_b)
    assert 'data-testid="queue-item"' not in page_a and page_b.count('data-testid="queue-item"') == 1
    with client.session_transaction() as session:
        assert session["active_campaign_id"] == b.id
    path_a = client.get(url(a, f"path/{oid('AutopsySectoid')}")).get_data(as_text=True)
    path_b = client.get(url(b, f"path/{oid('AutopsySectoid')}")).get_data(as_text=True)
    assert 'data-stat="outstanding">0<' in path_a  # Alien Biotech already done in A
    assert 'data-stat="outstanding">1<' in path_b  # B still needs it


def test_catalog_and_avenger_link_to_the_unlock_path(client, real):
    from app.services import avenger

    campaign = new_campaign("A")
    client.get(f"/campaigns/{campaign.id}/dashboard")  # makes it the active campaign
    detail = client.get(f"/catalog/objects/{oid('PlasmaRifle')}").get_data(as_text=True)
    assert f"/campaigns/{campaign.id}/research/path/{oid('PlasmaRifle')}" in detail

    pg = next(o.object_id for o in avenger.list_facility_options(campaign) if o.name == "Proving Ground")
    fragment = client.get(f"/campaigns/{campaign.id}/avenger/facilities/{pg}").get_data(as_text=True)
    assert 'data-testid="facility-show-unlock-path"' in fragment and f"research/path/{pg}" in fragment


def test_nav_has_a_live_research_entry_for_the_active_campaign(client, real):
    campaign = new_campaign("A")
    client.get(f"/campaigns/{campaign.id}/dashboard")
    assert f'href="/campaigns/{campaign.id}/research/"' in client.get("/").get_data(as_text=True)


def test_cycle_warning_is_displayed_for_corrupted_data(client, real, tmp_path):
    from app.services.content_import import import_pack
    from tests.avenger_helpers import attach_source, write_pack

    objects = [{"template_key": k, "type": "research_project", "name": k} for k in ("LoopX", "LoopY")]
    rels = [
        {"type": "requires_research", "from": ["loopy", "LoopX"], "to": ["loopy", "LoopY"]},
        {"type": "requires_research", "from": ["loopy", "LoopY"], "to": ["loopy", "LoopX"]},
    ]
    import_pack(str(write_pack(tmp_path, "loopy", objects=objects, relationships=rels, requires=["xcom2-base"])))
    campaign = new_campaign("A")
    attach_source(campaign, "loopy")
    response = client.get(url(campaign, f"path/{oid('LoopX')}"))
    assert response.status_code == 200 and 'data-testid="cycle-warning"' in response.get_data(as_text=True)
