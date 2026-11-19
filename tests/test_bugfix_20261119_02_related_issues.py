"""Regression tests for the three related issues recorded in the report for
Bug fixes - 19/11/2026 - 01, section 10.

1. soldiers/form.html rendered Python `None` as the text "None" for a soldier
   with no surname / callsign / notes (and an untouched Save then stored the
   word "None").
2. soldiers/form.html spliced a raw submitted request value into its
   `x-data="{ classId: ... }"` attribute (JavaScript injection into an Alpine
   expression), and the state it fed was never used to highlight the cards.
3. The build editor's Edit page ignored a base change: the step-1 cards were
   `?base_object_id=` links that only Create read, and a GET navigation would in
   any case discard everything typed so far.

Pages are parsed with a real HTML parser (see test_bugfix_20261119_01).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.avenger_helpers import attach_source, import_real_packs, new_campaign, write_pack
from tests.test_bugfix_20261119_01_build_editor import (
    NASTY_NAME,
    NASTY_NOTES,
    NASTY_PURPOSE,
    Page,
    _form_from_edit_page,
    _snapshot,
    edit_url,
    get_page,
    leaked_fragments,
    make_armor_build,
    make_weapon_build,
    oid,
)

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


@pytest.fixture()
def real(app, seeded_db):
    with app.app_context():
        import_real_packs()
        yield


def soldier_form(page: Page) -> dict:
    return next(a for a in page.tags("form") if a.get("data-testid") == "soldier-form")


def soldier_url(campaign, soldier=None, suffix="edit"):
    return f"/campaigns/{campaign.id}/soldiers/" + (f"{soldier.id}/{suffix}" if soldier else "new")


# =========================================================== 1. the text "None"


def _bare_soldier(campaign):
    from app.services import soldiers

    return soldiers.create_soldier(campaign, name="Jane", class_object_id=oid("Ranger"))


def _soldier_post_data(page: Page, **overrides) -> dict:
    data = {
        "name": page.input_value("name"), "surname": page.input_value("surname"),
        "callsign": page.input_value("callsign"), "notes": page.textareas.get("notes", ""),
        "status": "active",
    }
    chosen = soldier_form(page).get("data-class-id")
    if chosen:
        data["class_object_id"] = chosen
    data.update(overrides)
    return data


def test_soldier_edit_form_shows_empty_fields_not_the_text_none(client, real):
    campaign = new_campaign("A", "wotc-standard")
    soldier = _bare_soldier(campaign)
    assert (soldier.surname, soldier.callsign, soldier.notes) == (None, None, None)

    page = get_page(client, soldier_url(campaign, soldier))

    assert page.input_value("name") == "Jane"
    assert page.input_value("surname") == "" and page.input_value("callsign") == ""
    assert page.textareas.get("notes", "").strip() == ""
    assert "None" not in [a.get("value") for a in page.tags("input")]


def test_saving_a_soldier_with_empty_optional_fields_keeps_them_null(client, real):
    from app.extensions import db
    from app.models import CampaignSoldier

    campaign = new_campaign("A", "wotc-standard")
    soldier = _bare_soldier(campaign)
    page = get_page(client, soldier_url(campaign, soldier))

    response = client.post(soldier_url(campaign, soldier), data=_soldier_post_data(page))

    assert response.status_code == 302
    db.session.expire_all()
    saved = db.session.get(CampaignSoldier, soldier.id)
    assert (saved.surname, saved.callsign, saved.notes) == (None, None, None)  # never the string "None"
    assert saved.class_object_id == oid("Ranger")


def test_soldier_values_with_special_characters_round_trip_exactly(client, real):
    from app.extensions import db
    from app.models import CampaignSoldier

    campaign = new_campaign("A", "wotc-standard")
    soldier = _bare_soldier(campaign)
    data = _soldier_post_data(get_page(client, soldier_url(campaign, soldier)), surname=NASTY_NAME, callsign=NASTY_PURPOSE, notes=NASTY_NOTES)
    assert client.post(soldier_url(campaign, soldier), data=data).status_code == 302

    page = get_page(client, soldier_url(campaign, soldier))
    assert page.input_value("surname") == NASTY_NAME and page.input_value("callsign") == NASTY_PURPOSE
    assert page.textareas["notes"].strip("\n") == NASTY_NOTES
    assert not [a for a in page.tags("img") if a.get("src") == "x"]
    db.session.expire_all()
    assert db.session.get(CampaignSoldier, soldier.id).notes == NASTY_NOTES


def test_new_soldier_form_error_rerender_keeps_what_was_typed(client, real):
    campaign = new_campaign("A", "wotc-standard")
    response = client.post(soldier_url(campaign), data={"name": "  ", "surname": "O'Neil", "callsign": 'The "Viper"', "notes": "n<1>"})
    assert response.status_code == 400
    page = Page(response.get_data(as_text=True))
    assert "required" in page.text
    assert (page.input_value("surname"), page.input_value("callsign")) == ("O'Neil", 'The "Viper"')
    assert page.textareas["notes"] == "n<1>"


# ==================================================== 2. no raw value in x-data


def test_soldier_form_component_is_named_and_carries_only_a_validated_integer(client, real):
    campaign = new_campaign("A", "wotc-standard")
    soldier = _bare_soldier(campaign)
    for url in (soldier_url(campaign), soldier_url(campaign, soldier)):
        page = get_page(client, url)
        assert soldier_form(page)["x-data"] == "soldierForm"
        assert soldier_form(page).get("data-class-id", "") in ("", str(oid("Ranger")))
        assert leaked_fragments(page) == []
    assert soldier_form(get_page(client, soldier_url(campaign, soldier)))["data-class-id"] == str(oid("Ranger"))
    assert soldier_form(get_page(client, soldier_url(campaign)))["data-class-id"] == ""


HOSTILE = ["1});alert(document.cookie);//", '1" x-init="alert(1)" y="', "</script><script>alert(1)</script>", "1'];alert(1);//"]


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_class_or_rank_value_cannot_reach_javascript_or_markup(client, real, hostile):
    campaign = new_campaign("A", "wotc-standard")
    soldier = _bare_soldier(campaign)
    for url, data in (
        (soldier_url(campaign), {"name": "", "class_object_id": hostile, "rank_object_id": hostile}),  # create, forced error
        (soldier_url(campaign, soldier), {"name": "", "class_object_id": hostile, "rank_object_id": hostile}),  # edit, forced error
    ):
        response = client.post(url, data=data)
        assert response.status_code == 400
        html = response.get_data(as_text=True)
        page = Page(html)
        assert soldier_form(page)["x-data"] == "soldierForm"
        assert soldier_form(page).get("data-class-id", "") == "" or soldier_form(page)["data-class-id"].isdigit()
        assert "alert(" not in html and "x-init" not in html
        assert [a for a in page.tags("script") if a.get("src") is None and "application/json" not in a.get("type", "")] == []
        assert not [a for a in page.start_tags if "x-init" in a[1]]


def test_class_chosen_by_the_user_survives_a_validation_error_on_edit(client, real):
    campaign = new_campaign("A", "wotc-standard")
    soldier = _bare_soldier(campaign)  # saved as Ranger
    grenadier = oid("Grenadier")

    response = client.post(soldier_url(campaign, soldier), data={"name": "", "class_object_id": str(grenadier), "rank_object_id": ""})

    assert response.status_code == 400
    page = Page(response.get_data(as_text=True))
    assert soldier_form(page)["data-class-id"] == str(grenadier)  # what they picked, not what was saved
    checked = [a for a in page.tags("input") if a.get("name") == "class_object_id" and "checked" in a]
    assert [a["value"] for a in checked] == [str(grenadier)]


def test_class_cards_are_server_selected_and_state_driven(client, real):
    campaign = new_campaign("A", "wotc-standard")
    page = get_page(client, soldier_url(campaign, _bare_soldier(campaign)))
    cards = [a for a in page.tags("label") if "sd-pick-card" in a.get("class", "")]
    assert cards and sum("is-selected" in a["class"] for a in cards) == 1
    assert all(re.fullmatch(r"\{ 'is-selected': classId === \d+ \}", a[":class"]) for a in cards)  # integers only
    radios = [a for a in page.tags("input") if a.get("name") == "class_object_id"]
    assert all(a.get("x-model.number") == "classId" for a in radios)


def test_no_template_interpolates_request_derived_values_into_x_data():
    """Alpine evaluates x-data as JavaScript, so a request-derived value in it is
    script injection. Existing, integer-only interpolations are listed so the
    debt is visible; anything new fails."""
    allowed = {"avenger/_cell_modal.html", "campaigns/sources.html"}  # database integers / fixed enum only
    offenders = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text()
        for m in re.finditer(r'x-data="([^"]*)"', text, re.DOTALL):
            value = m.group(1)
            if "{{" in value and str(path.relative_to(TEMPLATES)) not in allowed:
                offenders.append(f"{path.relative_to(TEMPLATES)}: {value[:70]!r}")
            if re.search(r"request\.|form\.get|request\.form", value):
                offenders.append(f"{path.relative_to(TEMPLATES)} (request value): {value[:70]!r}")
    assert not offenders, "\n".join(offenders)


# ======================================================== 3. changing the base


def _weapon_with_two(campaign, **kw):
    return make_weapon_build(
        campaign, attachments={("weapon_upgrade", 0): oid("ScopeUpgrade"), ("weapon_upgrade", 1): oid("AutoLoaderUpgrade")}, **kw
    )


def _rebase_post(client, campaign, build, new_base_key, **edits):
    page = get_page(client, edit_url(campaign, build))
    data = _form_from_edit_page(page)
    data.update(edits)
    data.pop("step", None)  # the Save button is not what was pressed
    data["rebase_to"] = str(oid(new_base_key))
    return client.post(edit_url(campaign, build), data=data)


def test_step_one_cards_are_submit_buttons_that_do_not_enforce_unrelated_validation(client, real):
    campaign = new_campaign("A", "wotc-standard")
    build = _weapon_with_two(campaign)
    for url in (edit_url(campaign, build), f"/campaigns/{campaign.id}/builds/weapon/new"):
        page = get_page(client, url)
        cards = [a for a in page.tags("button") if a.get("name") == "rebase_to"]
        assert len(cards) >= 10
        assert all("formnovalidate" in a and a["type"] == "submit" for a in cards)  # the empty required Name must not block it
        assert not [a for a in page.tags("a") if "base_object_id" in a.get("href", "")]


def test_edit_post_rebase_moves_to_the_new_base_and_keeps_the_draft(client, real):
    campaign = new_campaign("A", "wotc-standard")
    build = _weapon_with_two(campaign, name="Original", purpose="P", notes="N")
    before = _snapshot(build.id)

    response = _rebase_post(client, campaign, build, "Shotgun_CV", name=NASTY_NAME, purpose=NASTY_PURPOSE, notes=NASTY_NOTES)

    assert response.status_code == 200
    page = Page(response.get_data(as_text=True))
    assert leaked_fragments(page) == []
    state = page.state()
    assert state["baseId"] == oid("Shotgun_CV")
    assert state["attachments"] == [{"slotType": "weapon_upgrade", "index": 0, "objectId": oid("ScopeUpgrade")}]  # slot 2 no longer exists
    headings = [t.strip() for t in page.visible_text if re.fullmatch(r"\s*Weapon upgrade \d+\s*", t)]
    assert set(headings) == {"Weapon upgrade 1"}  # the new base has a single slot (listed in step 2 and step 3)
    # the user's unsaved draft comes back exactly
    assert page.input_value("name") == NASTY_NAME and page.input_value("purpose") == NASTY_PURPOSE
    assert page.textareas["notes"].strip("\n") == NASTY_NOTES
    assert "Auto-Loader" in page.text and "removed from this draft" in page.text
    assert _snapshot(build.id) == before  # a base change is a draft until Save


def test_rebase_then_save_updates_the_same_build_without_side_effects(client, real):
    from app.models import CampaignBuild, CampaignBuildSlot
    from app.services import soldiers

    campaign = new_campaign("A", "wotc-standard")
    build, bystander = _weapon_with_two(campaign, name="Mover"), make_weapon_build(campaign, name="Bystander")
    jane = soldiers.create_soldier(campaign, name="Jane")
    soldiers.assign_build(campaign, jane, build)
    bystander_before = _snapshot(bystander.id)
    builds_before = CampaignBuild.query.count()

    page = Page(_rebase_post(client, campaign, build, "Shotgun_CV", name="Mover Renamed").get_data(as_text=True))
    save = _form_from_edit_page(page)  # exactly what the re-rendered editor would submit
    response = client.post(edit_url(campaign, build), data=save)

    assert response.status_code == 302 and response.headers["Location"].endswith(f"/builds/{build.id}")
    after = _snapshot(build.id)
    assert after["base"] == oid("Shotgun_CV") and after["name"] == "Mover Renamed"
    assert after["slots"] == [("weapon_upgrade", 0, oid("ScopeUpgrade"), False)]
    assert CampaignBuild.query.count() == builds_before
    assert CampaignBuildSlot.query.filter_by(build_id=build.id).count() == 1
    assert _snapshot(bystander.id) == bystander_before
    assert soldiers.soldier_view(campaign, jane).assignment("weapon").id == build.id
    assert get_page(client, edit_url(campaign, build)).state()["baseId"] == oid("Shotgun_CV")


def test_rebase_to_a_base_with_no_slots_drops_everything_and_says_so(client, real):
    campaign = new_campaign("A", "wotc-standard")
    build = _weapon_with_two(campaign)
    page = Page(_rebase_post(client, campaign, build, "Pistol_CV").get_data(as_text=True))
    assert page.state()["attachments"] == [] and page.state()["baseId"] == oid("Pistol_CV")
    assert "Scope" in page.text and "Auto-Loader" in page.text and "removed from this draft" in page.text


def test_rebase_to_the_same_base_changes_nothing_and_shows_no_notice(client, real):
    campaign = new_campaign("A", "wotc-standard")
    build = _weapon_with_two(campaign)
    page = Page(_rebase_post(client, campaign, build, "Shotgun_MG").get_data(as_text=True))
    assert len(page.state()["attachments"]) == 2 and "removed from this draft" not in page.text


def test_rebase_on_an_armour_build(client, real):
    campaign = new_campaign("A", "wotc-standard")
    build = make_armor_build(campaign)  # Predator armour, two utility components
    page = get_page(client, edit_url(campaign, build))
    data = _form_from_edit_page(page)
    data.pop("step")
    data["rebase_to"] = str(oid("KevlarArmor"))
    rebased = Page(client.post(edit_url(campaign, build), data=data).get_data(as_text=True))
    assert rebased.state()["baseId"] == oid("KevlarArmor")
    kept = {a["index"] for a in rebased.state()["attachments"]}
    from app.services.builds import BuildContext, slot_specs

    assert kept <= {s.index for s in slot_specs(BuildContext(campaign), "armor", oid("KevlarArmor")) if s.slot_type == "utility"}


@pytest.mark.parametrize("bad", ["999999", "PredatorArmor"])
def test_rebase_to_an_invalid_base_is_rejected_and_nothing_changes(client, real, bad):
    campaign = new_campaign("A", "wotc-standard")
    build = _weapon_with_two(campaign)
    before = _snapshot(build.id)
    data = _form_from_edit_page(get_page(client, edit_url(campaign, build)))
    data.pop("step")
    data["rebase_to"] = str(oid(bad)) if bad == "PredatorArmor" else bad  # an armour is not a valid weapon base

    response = client.post(edit_url(campaign, build), data=data)

    assert response.status_code == 400
    page = Page(response.get_data(as_text=True))
    assert "available to this campaign" in page.text
    assert page.state()["baseId"] == oid("Shotgun_MG")  # stayed on the current base
    assert len(page.state()["attachments"]) == 2
    assert _snapshot(build.id) == before


def test_edit_deep_link_with_a_base_parameter_is_honoured_without_saving(client, real):
    campaign = new_campaign("A", "wotc-standard")
    build = _weapon_with_two(campaign)
    before = _snapshot(build.id)

    page = get_page(client, edit_url(campaign, build) + f"?base_object_id={oid('Shotgun_CV')}")

    assert page.state()["baseId"] == oid("Shotgun_CV") and len(page.state()["attachments"]) == 1
    assert "removed from this draft" in page.text
    assert page.input_value("name") == "Close Assault Mk II"  # saved values, nothing typed to preserve
    assert _snapshot(build.id) == before

    invalid = get_page(client, edit_url(campaign, build) + "?base_object_id=999999")
    assert invalid.state()["baseId"] == oid("Shotgun_MG") and len(invalid.state()["attachments"]) == 2
    assert "available to this campaign" in invalid.text


def test_manual_override_is_not_carried_to_a_different_base(client, real, tmp_path):
    """An override accepted for one base says nothing about another."""
    from app.services import builds
    from app.services.content_import import import_pack

    objects = [{"template_key": "SniperOnlyScope7", "type": "weapon_upgrade", "name": "Sniper-Only Scope 7",
                "attributes": {"compatible_weapon_categories": "sniper_rifle"}}]
    import_pack(str(write_pack(tmp_path, "scopes7", objects=objects, requires=["xcom2-base"])))
    campaign = new_campaign("A", "wotc-standard")
    attach_source(campaign, "scopes7")
    build = builds.create_build(
        campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="Overridden",
        attachments={("weapon_upgrade", 0): oid("SniperOnlyScope7")}, overrides={("weapon_upgrade", 0)},
    )
    before = _snapshot(build.id)
    data = _form_from_edit_page(get_page(client, edit_url(campaign, build)))
    assert data["override__weapon_upgrade__0"] == "1"
    data.pop("step")
    data["rebase_to"] = str(oid("Shotgun_MG"))

    rebased = Page(client.post(edit_url(campaign, build), data=data).get_data(as_text=True))

    assert rebased.state()["attachments"] == [{"slotType": "weapon_upgrade", "index": 0, "objectId": oid("SniperOnlyScope7")}]
    assert not [a for a in rebased.tags("input") if a.get("name", "").startswith("override__") and "checked" in a]
    # saving as-is is refused (still incompatible, no override) and nothing is stored
    refused = client.post(edit_url(campaign, build), data=_form_from_edit_page(rebased))
    assert refused.status_code == 400 and "not listed as compatible" in refused.get_data(as_text=True)
    assert _snapshot(build.id) == before


def test_create_post_rebase_keeps_the_typed_draft_and_get_deep_link_still_works(client, real):
    campaign = new_campaign("A", "wotc-standard")
    url = f"/campaigns/{campaign.id}/builds/weapon/new"
    csrf_stub = {"name": NASTY_NAME, "purpose": "p", "notes": NASTY_NOTES, "rebase_to": str(oid("Shotgun_MG"))}

    response = client.post(url, data=csrf_stub)

    assert response.status_code == 200
    page = Page(response.get_data(as_text=True))
    assert page.state()["baseId"] == oid("Shotgun_MG") and page.state()["step"] == 3
    assert page.input_value("name") == NASTY_NAME and page.textareas["notes"].strip("\n") == NASTY_NOTES
    assert get_page(client, url + f"?base_object_id={oid('Shotgun_MG')}").state()["baseId"] == oid("Shotgun_MG")

    bad = client.post(url, data={"name": "x", "rebase_to": "999999"})
    assert bad.status_code == 400 and "available to this campaign" in bad.get_data(as_text=True)


def test_rebase_post_is_csrf_protected_and_accepted_with_the_forms_token(app, real):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    campaign = new_campaign("A", "wotc-standard")
    build = _weapon_with_two(campaign)
    html = client.get(edit_url(campaign, build)).get_data(as_text=True)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
    data = {"base_object_id": str(oid("Shotgun_MG")), "name": "x", "rebase_to": str(oid("Shotgun_CV"))}

    assert client.post(edit_url(campaign, build), data=data).status_code == 400
    assert client.post(edit_url(campaign, build), data={**data, "csrf_token": "forged"}).status_code == 400
    assert client.post(edit_url(campaign, build), data={**data, "csrf_token": token}).status_code == 200


# --------------------------------------------------------- service-level rules


def test_fit_attachments_keeps_fitting_components_and_reports_the_rest(app, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    attachments = {("weapon_upgrade", 0): oid("ScopeUpgrade"), ("weapon_upgrade", 1): oid("AutoLoaderUpgrade"), ("utility", 0): oid("ScopeUpgrade")}

    fitted = builds.fit_attachments_to_base(campaign, "weapon", oid("Shotgun_CV"), attachments)

    assert fitted.attachments == {("weapon_upgrade", 0): oid("ScopeUpgrade")}
    assert sorted(fitted.dropped) == ["Auto-Loader", "Scope"]  # slot 2 and the nonexistent "utility" slot
    assert fitted.overrides == set()


def test_fit_attachments_drops_a_component_of_the_wrong_kind_for_the_slot(app, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    fitted = builds.fit_attachments_to_base(
        campaign, "weapon", oid("Shotgun_MG"), {("weapon_upgrade", 0): oid("PredatorArmor")}
    )
    assert fitted.attachments == {} and fitted.dropped == ("Predator Armor",)


def test_fit_attachments_rejects_an_invalid_base(app, real):
    from app.services import builds

    campaign = new_campaign("A", "wotc-standard")
    with pytest.raises(builds.BuildError):
        builds.fit_attachments_to_base(campaign, "weapon", oid("PredatorArmor"), {})
    with pytest.raises(builds.BuildError):
        builds.fit_attachments_to_base(campaign, "weapon", 999999, {})
