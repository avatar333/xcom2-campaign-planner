"""Regression tests for Bug fixes - 19/11/2026 - 01 (build editor, Edit page).

Root cause: `builds/editor.html` built its Alpine component inline:

    x-data="{ ... attachments: {{ attachments_list | tojson }}, get(...) {...} }"

Flask's `tojson` escapes `< > & '` but NOT the double quote, so as soon as a
saved build had any component the output contained `"weapon_upgrade"`. The
first `"` terminated the `x-data="..."` attribute, and the HTML parser then
treated the rest of the component source as stray attribute text until the
first `>` -- the `=>` of an arrow function -- after which everything else was
rendered as visible page text. Create was unaffected only because an empty
list (`[]`) contains no quotes.

Fix: the component lives in `static/js/build_editor.js` (`Alpine.data
("buildEditor")`), the server supplies only data as JSON in a
`<script type="application/json">` block (safe: `tojson` escapes `<`, `>`,
`&`, `'` and `</script>` cannot occur), and per-card identity travels in
`data-*` attributes instead of being spliced into JavaScript source.

These tests parse the real rendered HTML with a real HTML parser, so they
model what a browser does (this bug was invisible to substring assertions).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from tests.avenger_helpers import attach_source, import_real_packs, new_campaign, write_pack

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"

# Fragments of the component source that must never appear as visible text.
JS_FRAGMENTS = ("=> k[0]", "this.attachments", "slotType", "hit ? hit", "return hit", ".filter((", "choose(")


# ---------------------------------------------------------------- HTML model


class Page(HTMLParser):
    """Collects what a browser would show/attach: visible text, element
    attributes, form control values, and the JSON state block."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.visible_text: list[str] = []
        self.start_tags: list[tuple[str, dict]] = []
        self.scripts: dict[str, str] = {}
        self.textareas: dict[str, str] = {}
        self._stack: list[tuple[str, dict]] = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attributes = {k: ("" if v is None else v) for k, v in attrs}
        self.start_tags.append((tag, attributes))
        if tag not in ("input", "br", "img", "link", "meta", "hr"):
            self._stack.append((tag, attributes))

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        if not self._stack:
            return
        tag, attrs = self._stack[-1]
        if tag == "script":
            self.scripts[attrs.get("id", "")] = self.scripts.get(attrs.get("id", ""), "") + data
        elif tag == "textarea":
            self.textareas[attrs.get("id", "")] = self.textareas.get(attrs.get("id", ""), "") + data
        elif tag != "style":
            self.visible_text.append(data)

    # ---- convenience
    @property
    def text(self) -> str:
        return " ".join(self.visible_text)

    def tags(self, name):
        return [a for t, a in self.start_tags if t == name]

    def input_value(self, element_id):
        return next(a.get("value", "") for a in self.tags("input") if a.get("id") == element_id)

    def state(self) -> dict:
        return json.loads(self.scripts["bw-editor-state"])

    def form(self):
        return next(a for a in self.tags("form") if a.get("data-testid") == "build-editor")


def leaked_fragments(page: Page) -> list[str]:
    return [f for f in JS_FRAGMENTS if f in page.text]


# ------------------------------------------------------------------ fixtures


@pytest.fixture()
def real(app, seeded_db):
    with app.app_context():
        import_real_packs()
        yield


def by_name(name, type_slug):
    from app.models import CatalogObject, CatalogObjectType

    return (
        CatalogObject.query.join(CatalogObjectType, CatalogObjectType.id == CatalogObject.type_id)
        .filter(CatalogObject.name == name, CatalogObjectType.slug == type_slug)
        .first()
        .id
    )


def oid(key):
    from app.models import CatalogObject

    return CatalogObject.query.filter_by(template_key=key).first().id


def edit_url(campaign, build):
    return f"/campaigns/{campaign.id}/builds/{build.id}/edit"


def get_page(client, url) -> Page:
    response = client.get(url)
    assert response.status_code == 200
    return Page(response.get_data(as_text=True))


NASTY_NAME = 'Viper\'s "Close Assault" Build'
NASTY_PURPOSE = 'Mobility & <scouting> "fast" \'quiet\''
NASTY_NOTES = "Use <close-range> & flank when possible.\nSecond line \u2014 caf\u00e9 \u2603 </script><img src=x onerror=alert(1)>"


def make_weapon_build(campaign, *, name="Close Assault Mk II", purpose="Max crit", notes="Flank.", attachments=None):
    from app.services import builds

    return builds.create_build(
        campaign, "weapon", base_object_id=oid("Shotgun_MG"), name=name, purpose=purpose, notes=notes,
        attachments=attachments
        if attachments is not None
        else {("weapon_upgrade", 0): oid("LaserSightUpgrade_Superior"), ("weapon_upgrade", 1): oid("AutoLoaderUpgrade_Superior")},
    )


def make_armor_build(campaign, *, name="Mobile Scout", purpose="Scouting", notes="Stay quiet.", attachments=None):
    from app.services import builds

    return builds.create_build(
        campaign, "armor", base_object_id=oid("PredatorArmor"), name=name, purpose=purpose, notes=notes,
        attachments=attachments
        if attachments is not None
        else {("utility", 0): by_name("Medikit", "utility_item"), ("utility", 1): by_name("Mindshield", "utility_item")},
    )


MAKERS = {"weapon": make_weapon_build, "armor": make_armor_build}


# ------------------------------------------------------- the guards themselves


def test_leak_detector_catches_the_original_broken_markup():
    """Proves the parser model reproduces the reported symptom, so the other
    tests would have failed before the fix."""
    broken = (
        '<form x-data="{ step: 3, attachments: [[["weapon_upgrade", 0], 56]],\n'
        "  get(slotType, idx) { const hit = this.attachments.find(([k]) => k[0] === slotType && k[1] === idx); "
        'return hit ? hit[1] : null; } }"><nav>1. Choose armour</nav></form>'
    )
    page = Page(broken)
    assert leaked_fragments(page), "the original markup should leak component source into visible text"
    assert "1. Choose armour" in page.text


def test_no_template_puts_tojson_outside_a_script_element():
    """`tojson` does not escape double quotes, so it is only safe inside a
    <script> element -- never inside an HTML attribute. Guards the whole class."""
    offenders = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text()
        script_spans = [m.span() for m in re.finditer(r"<script\b[^>]*>.*?</script>", text, re.DOTALL | re.IGNORECASE)]
        for m in re.finditer(r"tojson", text):
            if not any(a <= m.start() < b for a, b in script_spans):
                offenders.append(f"{path.relative_to(TEMPLATES)}:{text[:m.start()].count(chr(10)) + 1}")
    assert not offenders, "tojson used outside <script>: " + ", ".join(offenders)


def test_editor_template_has_no_inline_component_source():
    text = (TEMPLATES / "builds" / "editor.html").read_text()
    assert 'x-data="buildEditor"' in text
    assert "x-data=\"{" not in text and "this.attachments" not in text and "=> k[0]" not in text


# ------------------------------------------------------------------ rendering


@pytest.mark.parametrize("kind", ["weapon", "armor"])
def test_edit_page_renders_cleanly_with_the_expected_editor(client, real, kind):
    campaign = new_campaign("A", "wotc-standard")
    build = MAKERS[kind](campaign)
    response = client.get(edit_url(campaign, build))
    assert response.status_code == 200

    page = Page(response.get_data(as_text=True))
    assert leaked_fragments(page) == []
    assert page.form()["x-data"] == "buildEditor"
    assert "Edit" in page.text and ("Weapon Build" if kind == "weapon" else "Armour Build") in page.text
    for step_label in ("Slots", "Components", "Name & Purpose"):
        assert step_label in page.text
    assert [a for a in page.tags("section") if a.get("data-testid") == "step-components"]


@pytest.mark.parametrize("kind", ["weapon", "armor"])
def test_create_page_renders_cleanly(client, real, kind):
    campaign = new_campaign("A", "wotc-standard")
    page = get_page(client, f"/campaigns/{campaign.id}/builds/{kind}/new")
    assert leaked_fragments(page) == []
    assert page.form()["x-data"] == "buildEditor"


# ------------------------------------------------------------- existing state


def test_weapon_edit_preloads_the_saved_build(client, real):
    campaign = new_campaign("A", "wotc-standard")
    build = make_weapon_build(campaign)
    page = get_page(client, edit_url(campaign, build))

    assert page.state() == {
        "step": 3,
        "baseId": oid("Shotgun_MG"),
        "attachments": [
            {"slotType": "weapon_upgrade", "index": 0, "objectId": oid("LaserSightUpgrade_Superior")},
            {"slotType": "weapon_upgrade", "index": 1, "objectId": oid("AutoLoaderUpgrade_Superior")},
        ],
    }
    assert page.input_value("name") == "Close Assault Mk II"
    assert page.input_value("purpose") == "Max crit"
    assert page.textareas["notes"].strip() == "Flank."
    labels = {(a["data-slot-type"], a["data-slot-index"], a["data-object-id"]) for a in page.tags("label") if "data-slot-type" in a}
    assert ("weapon_upgrade", "0", str(oid("LaserSightUpgrade_Superior"))) in labels  # the chosen card exists to be highlighted
    assert ("weapon_upgrade", "0", "") in labels  # and so does "None"


def test_armour_edit_preloads_the_saved_build(client, real):
    campaign = new_campaign("A", "wotc-standard")
    build = make_armor_build(campaign)
    page = get_page(client, edit_url(campaign, build))

    assert page.state() == {
        "step": 3,
        "baseId": oid("PredatorArmor"),
        "attachments": [
            {"slotType": "utility", "index": 0, "objectId": by_name("Medikit", "utility_item")},
            {"slotType": "utility", "index": 1, "objectId": by_name("Mindshield", "utility_item")},
        ],
    }
    assert page.input_value("name") == "Mobile Scout"
    assert page.input_value("purpose") == "Scouting"


def test_partially_filled_and_empty_builds_preload_only_what_exists(client, real):
    campaign = new_campaign("A", "wotc-standard")
    partial = make_weapon_build(campaign, name="Partial", attachments={("weapon_upgrade", 1): oid("ScopeUpgrade")})
    empty = make_armor_build(campaign, name="Empty", attachments={})

    assert get_page(client, edit_url(campaign, partial)).state()["attachments"] == [
        {"slotType": "weapon_upgrade", "index": 1, "objectId": oid("ScopeUpgrade")}
    ]
    empty_state = get_page(client, edit_url(campaign, empty)).state()
    assert empty_state["attachments"] == [] and empty_state["baseId"] == oid("PredatorArmor")


# --------------------------------------------------- Create / Edit share a model


def test_create_and_edit_use_the_same_component_and_state_shape(client, real):
    campaign = new_campaign("A", "wotc-standard")
    weapon, armour = make_weapon_build(campaign), make_armor_build(campaign)
    pages = {
        "create-weapon": get_page(client, f"/campaigns/{campaign.id}/builds/weapon/new"),
        "create-armor": get_page(client, f"/campaigns/{campaign.id}/builds/armor/new"),
        "create-weapon-with-base": get_page(client, f"/campaigns/{campaign.id}/builds/weapon/new?base_object_id={oid('Shotgun_MG')}"),
        "edit-weapon": get_page(client, edit_url(campaign, weapon)),
        "edit-armor": get_page(client, edit_url(campaign, armour)),
    }
    for name, page in pages.items():
        assert page.form()["x-data"] == "buildEditor", name
        state = page.state()
        assert set(state) == {"step", "baseId", "attachments"}, name
        assert all(set(a) == {"slotType", "index", "objectId"} for a in state["attachments"]), name

    assert pages["create-weapon"].state() == {"step": 1, "baseId": None, "attachments": []}
    assert pages["create-weapon-with-base"].state() == {"step": 3, "baseId": oid("Shotgun_MG"), "attachments": []}


# ------------------------------------------------------------------- escaping


@pytest.mark.parametrize("kind", ["weapon", "armor"])
def test_special_characters_cannot_break_the_page_structure(client, real, kind):
    campaign = new_campaign("A", "wotc-standard")
    build = MAKERS[kind](campaign, name=NASTY_NAME, purpose=NASTY_PURPOSE, notes=NASTY_NOTES)
    response = client.get(edit_url(campaign, build))
    assert response.status_code == 200
    page = Page(response.get_data(as_text=True))

    assert leaked_fragments(page) == []
    assert page.form()["x-data"] == "buildEditor"
    assert page.input_value("name") == NASTY_NAME  # decoded by the parser exactly as a browser would
    assert page.input_value("purpose") == NASTY_PURPOSE
    assert page.textareas["notes"].strip("\n") == NASTY_NOTES
    assert not [a for a in page.tags("img") if a.get("src") == "x"], "notes must not inject an element"
    assert page.state()["attachments"]  # the JSON state block still parsed
    assert list(page.scripts).count("bw-editor-state") == 1


def test_hostile_slot_types_from_a_mod_cannot_break_out_of_markup_or_state(app, client, real, tmp_path):
    """Slot types are mod data. One containing quotes and `</script>` must stay
    inert text in data-* attributes and JSON, never JavaScript or markup."""
    from app.services import builds
    from app.services.content_import import import_pack

    evil = "x'y\"z</script><b>bold</b>"
    objects = [
        {
            "template_key": "EvilGun", "type": "weapon", "name": "Evil Gun",
            "attributes": {
                "weapon_category": "rifle", "upgrade_slots": 0,
                "build_slots": json.dumps([{"slot_type": evil, "count": 1, "accepts": ["weapon_upgrade"], "label": "Evil Slot"}]),
            },
        }
    ]
    import_pack(str(write_pack(tmp_path, "evilmod", objects=objects, requires=["xcom2-base"])))
    campaign = new_campaign("A", "wotc-standard")
    attach_source(campaign, "evilmod")
    build = builds.create_build(
        campaign, "weapon", base_object_id=oid("EvilGun"), name="Evil", attachments={(evil, 0): oid("ScopeUpgrade")}
    )

    page = get_page(client, edit_url(campaign, build))
    assert leaked_fragments(page) == []
    assert page.state()["attachments"] == [{"slotType": evil, "index": 0, "objectId": oid("ScopeUpgrade")}]
    assert evil in {a.get("data-slot-type") for a in page.tags("label")}
    assert not page.tags("b"), "a slot type must not inject markup"
    assert list(page.scripts).count("bw-editor-state") == 1


# ----------------------------------------------------------- saving / updating


def _snapshot(build_id):
    from app.extensions import db
    from app.models import CampaignBuild

    db.session.expire_all()
    build = db.session.get(CampaignBuild, build_id)
    return {
        "name": build.name, "purpose": build.purpose, "notes": build.notes, "base": build.base_object_id,
        "slots": [(s.slot_type, s.slot_index, s.attached_object_id, bool(s.is_override)) for s in build.slots],
    }


def _form_from_edit_page(page: Page) -> dict:
    """What a browser would submit for the Edit form, built only from what the
    server rendered (no knowledge of the saved build)."""
    state = page.state()
    data = {"step": "save", "base_object_id": str(state["baseId"]), "name": page.input_value("name"),
            "purpose": page.input_value("purpose"), "notes": page.textareas.get("notes", "")}
    for a in state["attachments"]:
        data[f"slot__{a['slotType']}__{a['index']}"] = str(a["objectId"])
    for attrs in page.tags("input"):
        if attrs.get("type") == "checkbox" and "checked" in attrs and attrs.get("name", "").startswith("override__"):
            data[attrs["name"]] = "1"
    return data


@pytest.mark.parametrize("kind", ["weapon", "armor"])
def test_saving_without_changes_leaves_the_build_exactly_as_it_was(client, real, kind):
    from app.models import CampaignBuild

    campaign = new_campaign("A", "wotc-standard")
    build = MAKERS[kind](campaign, name=NASTY_NAME, purpose=NASTY_PURPOSE, notes="Plain notes")
    other = MAKERS[kind](campaign, name="Bystander")
    before, other_before = _snapshot(build.id), _snapshot(other.id)
    count_before = CampaignBuild.query.count()

    data = _form_from_edit_page(get_page(client, edit_url(campaign, build)))
    response = client.post(edit_url(campaign, build), data=data)

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/builds/{build.id}")  # same record, not a new build
    assert CampaignBuild.query.count() == count_before
    assert _snapshot(build.id) == before  # base, name, purpose, notes, components, order: all unchanged
    assert _snapshot(other.id) == other_before


def test_editing_a_build_updates_the_same_record_without_duplicates(client, real):
    from app.models import CampaignBuild, CampaignBuildSlot
    from app.services import builds, soldiers

    campaign = new_campaign("A", "wotc-standard")
    build = make_weapon_build(campaign)
    bystander = make_weapon_build(campaign, name="Bystander")
    bystander_before = _snapshot(bystander.id)
    jane = soldiers.create_soldier(campaign, name="Jane")
    soldiers.assign_build(campaign, jane, build)
    count_before, slots_before = CampaignBuild.query.count(), CampaignBuildSlot.query.count()

    data = _form_from_edit_page(get_page(client, edit_url(campaign, build)))
    data["slot__weapon_upgrade__0"] = str(oid("ScopeUpgrade"))  # change one component
    data["purpose"] = "Updated purpose"
    response = client.post(edit_url(campaign, build), data=data)
    assert response.status_code == 302

    after = _snapshot(build.id)
    assert after["slots"] == [("weapon_upgrade", 0, oid("ScopeUpgrade"), False), ("weapon_upgrade", 1, oid("AutoLoaderUpgrade_Superior"), False)]
    assert after["purpose"] == "Updated purpose" and after["name"] == "Close Assault Mk II" and after["base"] == oid("Shotgun_MG")
    assert CampaignBuild.query.count() == count_before and CampaignBuildSlot.query.count() == slots_before  # no duplicates
    assert _snapshot(bystander.id) == bystander_before
    assert soldiers.soldier_view(campaign, jane).assignment("weapon").id == build.id  # assignments untouched
    reopened = get_page(client, edit_url(campaign, build)).state()["attachments"]
    assert [(a["index"], a["objectId"]) for a in reopened] == [(0, oid("ScopeUpgrade")), (1, oid("AutoLoaderUpgrade_Superior"))]
    del builds


def test_removing_a_component_in_edit_persists(client, real):
    campaign = new_campaign("A", "wotc-standard")
    build = make_weapon_build(campaign)
    data = _form_from_edit_page(get_page(client, edit_url(campaign, build)))
    del data["slot__weapon_upgrade__1"]  # the user picked "None" for slot 2
    assert client.post(edit_url(campaign, build), data=data).status_code == 302
    assert [s[:2] for s in _snapshot(build.id)["slots"]] == [("weapon_upgrade", 0)]


def test_manual_compatibility_override_survives_an_edit_round_trip(client, real, tmp_path):
    """The saved override must come back ticked; otherwise an untouched Save
    would be rejected (or silently drop the component)."""
    from app.services import builds
    from app.services.content_import import import_pack

    objects = [{"template_key": "SniperOnlyScope9", "type": "weapon_upgrade", "name": "Sniper-Only Scope 9",
                "attributes": {"compatible_weapon_categories": "sniper_rifle"}}]
    import_pack(str(write_pack(tmp_path, "scopes9", objects=objects, requires=["xcom2-base"])))
    campaign = new_campaign("A", "wotc-standard")
    attach_source(campaign, "scopes9")
    build = builds.create_build(
        campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="Override Build",
        attachments={("weapon_upgrade", 0): oid("SniperOnlyScope9")}, overrides={("weapon_upgrade", 0)},
    )
    before = _snapshot(build.id)
    assert before["slots"] == [("weapon_upgrade", 0, oid("SniperOnlyScope9"), True)]

    page = get_page(client, edit_url(campaign, build))
    ticked = [a for a in page.tags("input") if a.get("name") == "override__weapon_upgrade__0" and "checked" in a]
    assert len(ticked) == 1

    response = client.post(edit_url(campaign, build), data=_form_from_edit_page(page))
    assert response.status_code == 302
    assert _snapshot(build.id) == before


@pytest.mark.parametrize("kind", ["weapon", "armor"])
def test_build_without_purpose_or_notes_is_not_corrupted_into_the_text_none(client, real, kind):
    """NULL purpose/notes must render as empty fields; rendering Python's
    `None` would turn an untouched Save into the literal string "None"."""
    from app.extensions import db
    from app.models import CampaignBuild

    campaign = new_campaign("A", "wotc-standard")
    build = MAKERS[kind](campaign, purpose=None, notes=None)
    assert (build.purpose, build.notes) == (None, None)

    page = get_page(client, edit_url(campaign, build))
    assert page.input_value("purpose") == "" and page.textareas.get("notes", "").strip() == ""

    response = client.post(edit_url(campaign, build), data=_form_from_edit_page(page))
    assert response.status_code == 302
    db.session.expire_all()
    saved = db.session.get(CampaignBuild, build.id)
    assert (saved.purpose, saved.notes) == (None, None)


def test_failed_save_rerenders_with_the_users_selections_and_valid_markup(client, real):
    campaign = new_campaign("A", "wotc-standard")
    make_weapon_build(campaign, name="Taken Name")
    build = make_weapon_build(campaign, name="Other Name")
    data = _form_from_edit_page(get_page(client, edit_url(campaign, build)))
    data["name"] = "Taken Name"  # duplicate -> validation error
    data["slot__weapon_upgrade__0"] = str(oid("ScopeUpgrade"))

    response = client.post(edit_url(campaign, build), data=data)

    assert response.status_code == 400
    page = Page(response.get_data(as_text=True))
    assert leaked_fragments(page) == [] and "already has a" in page.text
    assert {"slotType": "weapon_upgrade", "index": 0, "objectId": oid("ScopeUpgrade")} in page.state()["attachments"]
    assert _snapshot(build.id)["name"] == "Other Name"  # nothing was saved


# ------------------------------------------------------------ create regression


@pytest.mark.parametrize(
    ("kind", "base_key", "attachments"),
    [("weapon", "Shotgun_MG", {"slot__weapon_upgrade__0": "ScopeUpgrade"}), ("armor", "PredatorArmor", {})],
)
def test_create_still_works_for_weapon_and_armour(client, real, kind, base_key, attachments):
    from app.models import CampaignBuild

    campaign = new_campaign("A", "wotc-standard")
    data = {"step": "save", "base_object_id": str(oid(base_key)), "name": NASTY_NAME, "purpose": NASTY_PURPOSE, "notes": NASTY_NOTES}
    data.update({k: str(oid(v)) for k, v in attachments.items()})

    response = client.post(f"/campaigns/{campaign.id}/builds/{kind}/new", data=data)

    assert response.status_code == 302
    build = CampaignBuild.query.filter_by(campaign_id=campaign.id, build_type=kind).one()
    assert (build.name, build.purpose, build.notes, build.base_object_id) == (NASTY_NAME, NASTY_PURPOSE, NASTY_NOTES, oid(base_key))
    assert len(build.slots) == len(attachments)
    assert Page(client.get(edit_url(campaign, build)).get_data(as_text=True)).state()["baseId"] == oid(base_key)


# --------------------------------------------------------------- the JS itself


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_build_editor_component_logic():
    script = ROOT / "tests" / "js" / "build_editor.test.js"
    result = subprocess.run(["node", str(script)], check=False, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "OK" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_build_editor_script_parses():
    result = subprocess.run(["node", "--check", str(ROOT / "app" / "static" / "js" / "build_editor.js")], check=False, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
