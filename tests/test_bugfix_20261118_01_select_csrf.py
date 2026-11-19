"""Regression tests for Bug fixes - 18/11/2026 - 01.

Root cause: the "Select" button on the Campaigns list (`campaigns/list.html`)
posted through a plain `<form>` with no CSRF token, unlike every other
mutating form in the app (which either call `form.hidden_tag()` for a real
WTForms object, or include the established
`<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` pattern).
With CSRF protection enabled (the default, `WTF_CSRF_ENABLED = True`), Flask-WTF
correctly rejected the request with 400 "The CSRF token is missing."

The project's `TestingConfig` disables CSRF globally (`WTF_CSRF_ENABLED =
False`), which is why the pre-existing `test_select_campaign_sets_active_context`
in `test_routes.py` never caught this: it exercises the route logic but never
exercises CSRF enforcement. These tests explicitly re-enable
`WTF_CSRF_ENABLED` on the already-constructed test app (never touching the
global `CSRFProtect` extension, never adding an exemption) so the real
protection path is under test, matching the bug report's requirement to never
rely on CSRF being disabled.
"""
from __future__ import annotations

import re

import pytest

from app.repositories import campaigns as campaigns_repo


@pytest.fixture()
def csrf_client(app):
    """The same app/client as every other test, but with real CSRF
    enforcement switched on -- never a global or route-level exemption."""
    app.config["WTF_CSRF_ENABLED"] = True
    return app.test_client()


def _extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match, "no csrf_token hidden input found in the rendered page"
    return match.group(1)


def _token_for_campaigns_list(csrf_client) -> str:
    page = csrf_client.get("/campaigns/").get_data(as_text=True)
    return _extract_csrf_token(page)


def _table_row_for(html: str, campaign_name: str) -> str:
    """The `<tr>...</tr>` for `campaign_name` in the campaigns table -- never
    the topbar's "Active Campaign" indicator, which also names the campaign
    and appears earlier in the page."""
    table = html.split('<table class="data-table">', 1)[1]
    start = table.index(campaign_name)
    row_start = table.rindex("<tr>", 0, start)
    row_end = table.index("</tr>", start) + len("</tr>")
    return table[row_start:row_end]


# --------------------------------------------------------------------- CSRF


def test_select_campaign_rejects_request_with_no_csrf_token(app, csrf_client, seeded_db):
    """Reproduces the exact reported failure: POSTing Select with no token
    must still be rejected -- CSRF protection must remain fully active."""
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="No Token", difficulty="commander")
        campaign_id = campaign.id

    response = csrf_client.post(f"/campaigns/{campaign_id}/select", data={})

    assert response.status_code == 400
    assert b"CSRF" in response.data


def test_select_campaign_succeeds_with_valid_csrf_token_from_the_rendered_page(app, csrf_client, seeded_db):
    """The fix: the Select form now renders a real CSRF token, and submitting
    it (exactly as a browser would) is accepted."""
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="Has Token", difficulty="commander")
        campaign_id = campaign.id

    list_page = csrf_client.get("/campaigns/").get_data(as_text=True)
    assert f'action="/campaigns/{campaign_id}/select"' in list_page
    token = _extract_csrf_token(list_page)

    response = csrf_client.post(
        f"/campaigns/{campaign_id}/select",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)
    with csrf_client.session_transaction() as session:
        assert session["active_campaign_id"] == campaign_id


def test_select_campaign_rejects_forged_or_wrong_csrf_token(app, csrf_client, seeded_db):
    """A token that is present but invalid must still be rejected -- proves
    the fix does not accidentally weaken validation to "any value passes"."""
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="Forged Token", difficulty="commander")
        campaign_id = campaign.id

    response = csrf_client.post(
        f"/campaigns/{campaign_id}/select",
        data={"csrf_token": "not-a-real-token"},
    )

    assert response.status_code == 400
    with csrf_client.session_transaction() as session:
        assert session.get("active_campaign_id") != campaign_id


def test_other_campaign_forms_already_include_a_csrf_token(csrf_client, app, seeded_db):
    """Guards against regressing the forms that were already correct (the
    create/edit forms use `form.hidden_tag()`); confirms the established
    project-wide pattern this fix follows is itself intact."""
    page = csrf_client.get("/campaigns/new").get_data(as_text=True)
    assert "csrf_token" in page


# ------------------------------------------------------- campaign selection


def test_selecting_campaign_b_activates_it_and_deactivates_campaign_a(app, csrf_client, seeded_db):
    with app.app_context():
        campaign_a = campaigns_repo.create_campaign(name="Campaign A", difficulty="commander")
        campaign_b = campaigns_repo.create_campaign(name="Campaign B", difficulty="commander")
        a_id, b_id = campaign_a.id, campaign_b.id

    token = _token_for_campaigns_list(csrf_client)
    csrf_client.post(f"/campaigns/{a_id}/select", data={"csrf_token": token})
    with csrf_client.session_transaction() as session:
        assert session["active_campaign_id"] == a_id

    token = _token_for_campaigns_list(csrf_client)
    response = csrf_client.post(f"/campaigns/{b_id}/select", data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code in (302, 303)
    with csrf_client.session_transaction() as session:
        active = session["active_campaign_id"]
        assert active == b_id  # B is now active
        assert active != a_id  # A is no longer active (single active slot)


def test_active_campaign_state_persists_across_a_subsequent_request(app, csrf_client, seeded_db):
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="Persisted", difficulty="commander")
        campaign_id = campaign.id

    token = _token_for_campaigns_list(csrf_client)
    csrf_client.post(f"/campaigns/{campaign_id}/select", data={"csrf_token": token})

    # A brand new request (e.g. a page reload) must still see it active.
    list_page = csrf_client.get("/campaigns/").get_data(as_text=True)
    assert 'disabled title="This campaign is already active"' in _table_row_for(list_page, "Persisted")
    dashboard_page = csrf_client.get("/").get_data(as_text=True)
    assert "Persisted" in dashboard_page


def test_selecting_campaign_does_not_touch_catalog_or_other_campaign_data(app, csrf_client, seeded_db):
    """Selection is a session-only context switch; it must not write to the
    database at all, so catalog data and every other campaign's own data stay
    byte-for-byte untouched."""
    from app.extensions import db
    from app.models import Campaign, CatalogObjectType

    with app.app_context():
        campaign_a = campaigns_repo.create_campaign(name="Campaign A", difficulty="commander", notes="Keep me")
        campaign_b = campaigns_repo.create_campaign(name="Campaign B", difficulty="veteran")
        a_id, b_id = campaign_a.id, campaign_b.id
        a_updated_at_before = campaign_a.updated_at
        catalog_type_count_before = CatalogObjectType.query.count()

    token = _token_for_campaigns_list(csrf_client)
    csrf_client.post(f"/campaigns/{b_id}/select", data={"csrf_token": token})

    with app.app_context():
        db.session.expire_all()
        reloaded_a = db.session.get(Campaign, a_id)
        assert reloaded_a.notes == "Keep me"
        assert reloaded_a.updated_at == a_updated_at_before  # untouched, not just unchanged in value
        assert reloaded_a.difficulty == "commander"
        reloaded_b = db.session.get(Campaign, b_id)
        assert reloaded_b.difficulty == "veteran"
        assert CatalogObjectType.query.count() == catalog_type_count_before


def test_select_campaign_404_for_missing_campaign_with_csrf_enabled(app, csrf_client, seeded_db):
    with app.app_context():
        campaigns_repo.create_campaign(name="Just For A Token", difficulty="commander")
    token = _token_for_campaigns_list(csrf_client)
    response = csrf_client.post("/campaigns/999999/select", data={"csrf_token": token})
    assert response.status_code == 404


# ------------------------------------------------------------------------ UI


def test_inactive_campaign_shows_a_functional_select_form(csrf_client, app, seeded_db):
    with app.app_context():
        campaigns_repo.create_campaign(name="Still Inactive", difficulty="commander")

    html = csrf_client.get("/campaigns/").get_data(as_text=True)
    row = _table_row_for(html, "Still Inactive")

    assert "<form" in row and "Select</button>" in row
    assert "csrf_token" in row
    assert "disabled" not in row.split("</form>")[0]


def test_active_campaign_shows_no_functional_select_action(app, csrf_client, seeded_db):
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="Currently Active", difficulty="commander")
        campaign_id = campaign.id

    token = _token_for_campaigns_list(csrf_client)
    csrf_client.post(f"/campaigns/{campaign_id}/select", data={"csrf_token": token})

    html = csrf_client.get("/campaigns/").get_data(as_text=True)
    row = _table_row_for(html, "Currently Active")

    assert f'action="/campaigns/{campaign_id}/select"' not in row  # no POST target for the active campaign
    assert "disabled" in row
    assert "Active" in row  # ACTIVE status indicator retained


def test_active_status_badge_and_disabled_action_coexist(app, csrf_client, seeded_db):
    """The ACTIVE badge (page-header-style indicator) and the disabled Select
    control are two separate, compatible pieces of UI -- both must be present."""
    with app.app_context():
        campaign = campaigns_repo.create_campaign(name="Badge And Button", difficulty="commander")
        campaign_id = campaign.id

    token = _token_for_campaigns_list(csrf_client)
    csrf_client.post(f"/campaigns/{campaign_id}/select", data={"csrf_token": token})

    html = csrf_client.get("/campaigns/").get_data(as_text=True)
    section = _table_row_for(html, "Badge And Button")

    assert "badge--ready" in section  # the existing "Active" badge, unchanged
    assert "<button" in section and 'disabled' in section
