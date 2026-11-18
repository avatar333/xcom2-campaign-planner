"""Route smoke tests for every nav destination + campaign CRUD HTTP flows
(IMPLEMENTATION_PLAN.md Phase 2 acceptance criteria)."""
from __future__ import annotations

from app.models import Campaign
from app.repositories import campaigns as campaigns_repo


def test_dashboard_route(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Dashboard" in response.data


def test_campaigns_list_route(client):
    response = client.get("/campaigns/")
    assert response.status_code == 200
    assert b"Campaigns" in response.data


def test_catalog_browse_route(client, seeded_db):
    response = client.get("/catalog/")
    assert response.status_code == 200
    assert b"Catalog" in response.data


def test_planning_placeholder_route(client):
    response = client.get("/planning/")
    assert response.status_code == 200
    assert b"Planning" in response.data
    assert b"Phase 7" in response.data


def test_new_campaign_form_renders(client):
    response = client.get("/campaigns/new")
    assert response.status_code == 200
    assert b"Create Campaign" in response.data


def test_create_campaign_via_post_redirects_and_persists(client, app, seeded_db):
    response = client.post(
        "/campaigns/new",
        data={
            "name": "Operation Testbed",
            "game_profile_slug": "wotc-standard",
            "difficulty": "veteran",
            "notes": "",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    with app.app_context():
        campaigns = Campaign.query.filter_by(name="Operation Testbed").all()
        assert len(campaigns) == 1


def test_create_campaign_via_post_rejects_blank_name(client, seeded_db):
    response = client.post(
        "/campaigns/new",
        data={"name": "   ", "game_profile_slug": "", "difficulty": "commander"},
    )
    assert response.status_code == 200
    assert b"required" in response.data.lower()


def test_campaign_dashboard_route(client, app, seeded_db):
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="Dashboard Check", difficulty="commander")
        campaign_id = campaign.id

    response = client.get(f"/campaigns/{campaign_id}/dashboard")
    assert response.status_code == 200
    assert b"Dashboard Check" in response.data


def test_campaign_dashboard_404_for_missing_campaign(client):
    response = client.get("/campaigns/999999/dashboard")
    assert response.status_code == 404


def test_edit_campaign_route_updates(client, app, seeded_db):
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="To Rename", difficulty="commander")
        campaign_id = campaign.id

    response = client.post(
        f"/campaigns/{campaign_id}/edit",
        data={"name": "Renamed", "game_profile_slug": "", "difficulty": "legend"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    with app.app_context():
        reloaded = campaigns_repo.get_campaign(campaign_id)
        assert reloaded.name == "Renamed"
        assert reloaded.difficulty == "legend"


def test_delete_campaign_requires_typed_name_confirmation(client, app, seeded_db):
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="Delete Me", difficulty="commander")
        campaign_id = campaign.id

    wrong = client.post(f"/campaigns/{campaign_id}/delete", data={"confirm_name": "wrong"})
    assert wrong.status_code == 400

    with app.app_context():
        assert campaigns_repo.get_campaign(campaign_id) is not None

    correct = client.post(
        f"/campaigns/{campaign_id}/delete",
        data={"confirm_name": "Delete Me"},
        follow_redirects=False,
    )
    assert correct.status_code in (302, 303)

    with app.app_context():
        assert campaigns_repo.get_campaign(campaign_id) is None


def test_select_campaign_sets_active_context(client, app, seeded_db):
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="Context Check", difficulty="commander")
        campaign_id = campaign.id

    response = client.post(f"/campaigns/{campaign_id}/select", data={}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Context Check" in response.data

    # The shell's "active campaign" indicator should now show it on another page.
    dashboard = client.get("/")
    assert b"Active Campaign" in dashboard.data
    assert b"Context Check" in dashboard.data


def test_edit_campaign_form_renders_get(client, app, seeded_db):
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="Editable", difficulty="commander")
        campaign_id = campaign.id

    response = client.get(f"/campaigns/{campaign_id}/edit")
    assert response.status_code == 200
    assert b"Editable" in response.data


def test_edit_campaign_404_for_missing_campaign(client):
    response = client.get("/campaigns/999999/edit")
    assert response.status_code == 404


def test_delete_campaign_form_renders_get(client, app, seeded_db):
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="About To Go", difficulty="commander")
        campaign_id = campaign.id

    response = client.get(f"/campaigns/{campaign_id}/delete")
    assert response.status_code == 200
    assert b"About To Go" in response.data


def test_delete_campaign_404_for_missing_campaign(client):
    response = client.get("/campaigns/999999/delete")
    assert response.status_code == 404


def test_select_campaign_404_for_missing_campaign(client):
    response = client.post("/campaigns/999999/select")
    assert response.status_code == 404


def test_archive_campaign_route(client, app, seeded_db):
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="To Archive", difficulty="commander")
        campaign_id = campaign.id

    response = client.post(f"/campaigns/{campaign_id}/archive", follow_redirects=False)
    assert response.status_code in (302, 303)

    with app.app_context():
        reloaded = campaigns_repo.get_campaign(campaign_id)
        assert reloaded.status == "archived"


def test_archive_campaign_404_for_missing_campaign(client):
    response = client.post("/campaigns/999999/archive")
    assert response.status_code == 404


def test_create_campaign_htmx_returns_redirect_header(client, seeded_db):
    response = client.post(
        "/campaigns/new",
        data={"name": "Htmx Created", "game_profile_slug": "", "difficulty": "commander"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code in (200, 302, 303)
    assert response.headers.get("HX-Redirect") == "/campaigns/"


def test_create_campaign_htmx_invalid_returns_modal_fragment(client, seeded_db):
    response = client.post(
        "/campaigns/new",
        data={"name": "   ", "game_profile_slug": "", "difficulty": "commander"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert b"modal" in response.data.lower()


def test_edit_campaign_htmx_returns_redirect_header(client, app, seeded_db):
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="Htmx Edit Target", difficulty="commander")
        campaign_id = campaign.id

    response = client.post(
        f"/campaigns/{campaign_id}/edit",
        data={"name": "Htmx Edited", "game_profile_slug": "", "difficulty": "veteran"},
        headers={"HX-Request": "true"},
    )
    assert response.headers.get("HX-Redirect") == "/campaigns/"

    with app.app_context():
        assert campaigns_repo.get_campaign(campaign_id).name == "Htmx Edited"


def test_delete_campaign_htmx_wrong_confirmation_returns_fragment(client, app, seeded_db):
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="Htmx Delete Target", difficulty="commander")
        campaign_id = campaign.id

    response = client.post(
        f"/campaigns/{campaign_id}/delete",
        data={"confirm_name": "nope"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 400
    assert b"cancelled" in response.data.lower()


def test_delete_campaign_htmx_correct_confirmation_redirects(client, app, seeded_db):
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="Htmx Delete OK", difficulty="commander")
        campaign_id = campaign.id

    response = client.post(
        f"/campaigns/{campaign_id}/delete",
        data={"confirm_name": "Htmx Delete OK"},
        headers={"HX-Request": "true"},
    )
    assert response.headers.get("HX-Redirect") == "/campaigns/"

    with app.app_context():
        assert campaigns_repo.get_campaign(campaign_id) is None
