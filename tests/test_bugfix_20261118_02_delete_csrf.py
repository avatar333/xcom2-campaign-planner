"""Regression tests for Bug fixes - 18/11/2026 - 02.

Root cause: the campaign delete confirmation forms (`campaigns/delete.html`
and the htmx fragment `campaigns/_delete_modal.html`) posted without a CSRF
token, so with CSRF protection enabled Flask-WTF rejected every delete with
400 "The CSRF token is missing." (found while fixing bug 01; the test config
disables CSRF globally, which hid it). CSRF stays enabled here via the
`csrf_client` fixture; nothing is exempted.
"""
from __future__ import annotations

import re

import pytest

from app.repositories import campaigns as campaigns_repo

HTMX = {"HX-Request": "true"}


@pytest.fixture()
def csrf_client(app):
    app.config["WTF_CSRF_ENABLED"] = True
    return app.test_client()


def _token(html: str) -> str:
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    assert match, "no csrf_token input in the rendered delete form"
    return match.group(1)


def _make(app, name):
    with app.app_context():
        return campaigns_repo.create_campaign(name=name, difficulty="commander").id


def _exists(app, campaign_id):
    with app.app_context():
        return campaigns_repo.get_campaign(campaign_id) is not None


def test_delete_page_and_modal_both_render_a_csrf_token(app, csrf_client, seeded_db):
    campaign_id = _make(app, "Token Check")
    page = csrf_client.get(f"/campaigns/{campaign_id}/delete").get_data(as_text=True)
    modal = csrf_client.get(f"/campaigns/{campaign_id}/delete", headers=HTMX).get_data(as_text=True)
    assert _token(page) and _token(modal)
    assert 'data-modal-root' in modal and 'data-modal-root' not in page


def test_delete_without_csrf_token_is_still_rejected(app, csrf_client, seeded_db):
    campaign_id = _make(app, "Protected")
    response = csrf_client.post(f"/campaigns/{campaign_id}/delete", data={"confirm_name": "Protected"})
    assert response.status_code == 400 and b"CSRF" in response.data
    assert _exists(app, campaign_id)

    forged = csrf_client.post(
        f"/campaigns/{campaign_id}/delete", data={"confirm_name": "Protected", "csrf_token": "forged"}
    )
    assert forged.status_code == 400
    assert _exists(app, campaign_id)


@pytest.mark.parametrize("headers", [None, HTMX], ids=["full-page", "htmx-modal"])
def test_delete_with_token_from_rendered_form_succeeds(app, csrf_client, seeded_db, headers):
    campaign_id = _make(app, "Delete Me")
    form = csrf_client.get(f"/campaigns/{campaign_id}/delete", headers=headers).get_data(as_text=True)

    response = csrf_client.post(
        f"/campaigns/{campaign_id}/delete",
        data={"confirm_name": "Delete Me", "csrf_token": _token(form)},
        headers=headers,
    )

    assert response.status_code in (302, 303)
    assert not _exists(app, campaign_id)
    if headers:
        assert response.headers["HX-Redirect"].endswith("/campaigns/")


def test_wrong_typed_name_with_valid_token_still_blocks_deletion(app, csrf_client, seeded_db):
    campaign_id = _make(app, "Keep Me")
    form = csrf_client.get(f"/campaigns/{campaign_id}/delete", headers=HTMX).get_data(as_text=True)

    response = csrf_client.post(
        f"/campaigns/{campaign_id}/delete",
        data={"confirm_name": "nope", "csrf_token": _token(form)},
        headers=HTMX,
    )

    assert response.status_code == 400
    assert "does not match" in response.get_data(as_text=True)
    assert _token(response.get_data(as_text=True))  # the re-rendered error modal can be resubmitted
    assert _exists(app, campaign_id)


def test_deleting_one_campaign_leaves_another_untouched_and_clears_active(app, csrf_client, seeded_db):
    keep_id, doomed_id = _make(app, "Survivor"), _make(app, "Doomed")
    csrf_client.get(f"/campaigns/{doomed_id}/dashboard")  # make the doomed campaign active
    with csrf_client.session_transaction() as session:
        assert session["active_campaign_id"] == doomed_id

    form = csrf_client.get(f"/campaigns/{doomed_id}/delete").get_data(as_text=True)
    csrf_client.post(f"/campaigns/{doomed_id}/delete", data={"confirm_name": "Doomed", "csrf_token": _token(form)})

    assert not _exists(app, doomed_id) and _exists(app, keep_id)
    with csrf_client.session_transaction() as session:
        assert session.get("active_campaign_id") is None
