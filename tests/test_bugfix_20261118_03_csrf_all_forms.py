"""Regression tests for Bug fixes - 18/11/2026 - 03.

Root cause: eight POST forms in three templates (campaign sources: toggle,
remove, attach; catalog object detail: delete object, create overlay, remove
relationship (x2 lists); content source detail: delete source) were written
without a CSRF token, the same defect as bugs 01 and 02. The test config
disables CSRF globally, which is why nothing caught them.

Two kinds of protection are added:
1. A structural guard that scans every template and fails if ANY POST form
   (including htmx `hx-post` forms) lacks a CSRF token -- this prevents the
   whole class of bug, not just these eight forms.
2. Behavioural tests that submit each repaired form with CSRF enabled: a
   request without a valid token is rejected and changes nothing; the token
   taken from the rendered form is accepted and the action takes effect.

CSRF is never disabled or exempted: the `csrf_client` fixture only turns
enforcement ON for the test app.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.repositories import campaign_sources as campaign_sources_repo
from app.repositories import catalog_objects as objects_repo
from app.repositories import content_sources as sources_repo
from tests.avenger_helpers import import_real_packs, new_campaign

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
FORM_RE = re.compile(r"<form\b(.*?)>(.*?)</form>", re.DOTALL | re.IGNORECASE)
TOKEN_RE = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')


# ------------------------------------------------------------ structural guard


def _post_forms(html: str):
    for match in FORM_RE.finditer(html):
        attrs, body = match.group(1), match.group(2)
        if re.search(r'method\s*=\s*"?post|hx-post', attrs, re.IGNORECASE):
            yield attrs, body


def test_every_post_form_in_every_template_carries_a_csrf_token():
    missing, checked = [], 0
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text()
        for attrs, body in _post_forms(text):
            checked += 1
            if not re.search(r"csrf_token|hidden_tag", body):
                missing.append(f"{path.relative_to(TEMPLATES)}: <form{' '.join(attrs.split())[:80]}")
    assert checked >= 35, "the scan found suspiciously few forms; the guard itself may be broken"
    assert not missing, "POST forms without a CSRF token:\n" + "\n".join(missing)


def test_the_structural_guard_detects_a_tokenless_form():
    bad = '<form method="post" action="/x"><button>Go</button></form>'
    good = '<form method="post" action="/x"><input type="hidden" name="csrf_token" value="t"></form>'
    htmx_bad = '<form hx-post="/x"><button>Go</button></form>'
    assert [bool(re.search("csrf_token", b)) for _a, b in _post_forms(bad + good + htmx_bad)] == [False, True, False]


# ------------------------------------------------------------------ helpers


@pytest.fixture()
def csrf_client(app):
    app.config["WTF_CSRF_ENABLED"] = True
    return app.test_client()


def _form_token(html: str, action: str, *, containing: str | None = None) -> str:
    """The CSRF token inside the specific form posting to `action`."""
    for attrs, body in _post_forms(html):
        if f'action="{action}"' in attrs and (containing is None or containing in body):
            match = TOKEN_RE.search(body)
            assert match, f"form posting to {action} has no csrf_token"
            return match.group(1)
    raise AssertionError(f"no POST form for {action} on the page")


def _oid(key):
    from app.models import CatalogObject

    return CatalogObject.query.filter_by(template_key=key).first().id


@pytest.fixture()
def real(app, seeded_db):
    with app.app_context():
        import_real_packs()
        yield


def _no_token_and_forged_are_rejected(client, url, data=None):
    for payload in ({}, {"csrf_token": "forged"}):
        response = client.post(url, data={**(data or {}), **payload})
        assert response.status_code == 400 and b"CSRF" in response.data


# ----------------------------------------------- campaign sources (3 forms)


def _campaign_with_sources_page(app, client):
    campaign = new_campaign("Sources Campaign", "wotc-standard")
    page = client.get(f"/campaigns/{campaign.id}/sources").get_data(as_text=True)
    return campaign, page


def test_toggle_source_requires_and_accepts_a_csrf_token(app, csrf_client, real):
    campaign, page = _campaign_with_sources_page(app, csrf_client)
    source = sources_repo.get_content_source_by_slug("wotc")
    url = f"/campaigns/{campaign.id}/sources/{source.id}/toggle"
    assert campaign_sources_repo.get_association(campaign.id, source.id).enabled

    _no_token_and_forged_are_rejected(csrf_client, url)
    assert campaign_sources_repo.get_association(campaign.id, source.id).enabled  # unchanged

    response = csrf_client.post(url, data={"csrf_token": _form_token(page, url)})
    assert response.status_code in (302, 303)
    from app.extensions import db

    db.session.expire_all()
    assert not campaign_sources_repo.get_association(campaign.id, source.id).enabled


def test_remove_source_requires_and_accepts_a_csrf_token(app, csrf_client, real):
    from app.extensions import db

    campaign, page = _campaign_with_sources_page(app, csrf_client)
    source = sources_repo.get_content_source_by_slug("wotc")
    url = f"/campaigns/{campaign.id}/sources/{source.id}/remove"

    _no_token_and_forged_are_rejected(csrf_client, url)
    assert campaign_sources_repo.get_association(campaign.id, source.id) is not None

    response = csrf_client.post(url, data={"csrf_token": _form_token(page, url)})
    assert response.status_code in (302, 303)
    db.session.expire_all()
    assert campaign_sources_repo.get_association(campaign.id, source.id) is None
    assert campaign_sources_repo.get_association(campaign.id, sources_repo.get_content_source_by_slug("xcom2-base").id)


def test_attach_source_requires_and_accepts_a_csrf_token(app, csrf_client, real):
    from app.extensions import db

    campaign, page = _campaign_with_sources_page(app, csrf_client)
    user_source = sources_repo.get_content_source_by_slug("user-local")
    url = f"/campaigns/{campaign.id}/sources/add"
    data = {"content_source_id": str(user_source.id)}

    _no_token_and_forged_are_rejected(csrf_client, url, data)
    assert campaign_sources_repo.get_association(campaign.id, user_source.id) is None

    token = _form_token(page, url, containing=f'value="{user_source.id}"')
    response = csrf_client.post(url, data={**data, "csrf_token": token})
    assert response.status_code in (302, 303)
    db.session.expire_all()
    assert campaign_sources_repo.get_association(campaign.id, user_source.id) is not None


def test_source_actions_stay_scoped_to_their_own_campaign(app, csrf_client, real):
    from app.extensions import db

    a, b = new_campaign("A", "wotc-standard"), new_campaign("B", "wotc-standard")
    wotc = sources_repo.get_content_source_by_slug("wotc")
    page_a = csrf_client.get(f"/campaigns/{a.id}/sources").get_data(as_text=True)
    url_a = f"/campaigns/{a.id}/sources/{wotc.id}/remove"

    csrf_client.post(url_a, data={"csrf_token": _form_token(page_a, url_a)})
    db.session.expire_all()
    assert campaign_sources_repo.get_association(a.id, wotc.id) is None
    assert campaign_sources_repo.get_association(b.id, wotc.id) is not None  # B untouched


# ------------------------------------------ catalog object detail (4 forms)


def _user_object(name="Custom Thing", type_slug="weapon"):
    from app.models import CatalogObjectType
    from app.services import catalog_objects as objects_service

    source = sources_repo.get_content_source_by_slug("user-local")
    object_type = CatalogObjectType.query.filter_by(slug=type_slug).first()
    return objects_service.create_object(
        source_id=source.id, type_id=object_type.id, name=name, is_user_authored=True
    )


def test_delete_object_requires_and_accepts_a_csrf_token(app, csrf_client, real):
    obj = _user_object("Doomed Object")
    obj_id = obj.id
    url = f"/catalog/objects/{obj_id}/delete"
    page = csrf_client.get(f"/catalog/objects/{obj_id}").get_data(as_text=True)

    _no_token_and_forged_are_rejected(csrf_client, url)
    assert objects_repo.get_object(obj_id) is not None

    response = csrf_client.post(url, data={"csrf_token": _form_token(page, url)})
    assert response.status_code in (302, 303)
    from app.extensions import db

    db.session.expire_all()
    assert objects_repo.get_object(obj_id) is None


def test_create_overlay_requires_and_accepts_a_csrf_token(app, csrf_client, real):
    from app.models import CatalogObject

    base_id = _oid("Workshop")
    url = f"/catalog/objects/{base_id}/overlay"
    page = csrf_client.get(f"/catalog/objects/{base_id}").get_data(as_text=True)

    _no_token_and_forged_are_rejected(csrf_client, url)
    assert CatalogObject.query.filter_by(overrides_object_id=base_id).count() == 0

    response = csrf_client.post(url, data={"csrf_token": _form_token(page, url)})
    assert response.status_code in (302, 303)
    overlays = CatalogObject.query.filter_by(overrides_object_id=base_id).all()
    assert len(overlays) == 1 and overlays[0].is_user_authored
    from app.extensions import db

    assert db.session.get(CatalogObject, base_id).is_user_authored is False  # the original is never mutated


@pytest.mark.parametrize("direction", ["outbound", "inbound"])
def test_remove_relationship_requires_and_accepts_a_csrf_token(app, csrf_client, real, direction):
    from app.extensions import db
    from app.models import CatalogObject, CatalogRelationship, CatalogRelationshipType
    from app.services import catalog_relationships as relationships_service

    source = sources_repo.get_content_source_by_slug("user-local")
    left, right = _user_object("Left Object"), _user_object("Right Object")
    relationship = relationships_service.create_relationship(
        relationship_type=CatalogRelationshipType.query.filter_by(slug="compatible_with").first(),
        source_object=db.session.get(CatalogObject, left.id),
        target_object=db.session.get(CatalogObject, right.id),
        source_pack_id=source.id,
    )
    relationship_id = relationship.id
    viewed = left.id if direction == "outbound" else right.id  # the Remove button appears in both lists
    url = f"/catalog/relationships/{relationship_id}/delete"
    page = csrf_client.get(f"/catalog/objects/{viewed}").get_data(as_text=True)

    _no_token_and_forged_are_rejected(csrf_client, url)
    assert db.session.get(CatalogRelationship, relationship_id) is not None

    response = csrf_client.post(url, data={"csrf_token": _form_token(page, url)})
    assert response.status_code in (302, 303)
    db.session.expire_all()
    assert db.session.get(CatalogRelationship, relationship_id) is None
    assert db.session.get(CatalogObject, left.id) and db.session.get(CatalogObject, right.id)  # objects untouched


# ---------------------------------------------- content source detail (1 form)


def test_delete_content_source_requires_and_accepts_a_csrf_token(app, csrf_client, seeded_db):
    from app.extensions import db
    from app.services.content_sources import create_content_source

    source = create_content_source(slug="deletable-mod", name="Deletable Mod", kind="workshop_mod")
    source_id = source.id
    url = f"/content/sources/{source_id}/delete"
    page = csrf_client.get(f"/content/sources/{source_id}").get_data(as_text=True)

    _no_token_and_forged_are_rejected(csrf_client, url)
    assert sources_repo.get_content_source(source_id) is not None

    response = csrf_client.post(url, data={"csrf_token": _form_token(page, url)})
    assert response.status_code in (302, 303)
    db.session.expire_all()
    assert sources_repo.get_content_source(source_id) is None
    assert sources_repo.get_content_source_by_slug("xcom2-base") is not None  # built-ins untouched
