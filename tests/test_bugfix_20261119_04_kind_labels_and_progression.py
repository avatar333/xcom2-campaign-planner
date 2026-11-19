"""Regression tests for the two related issues recorded in the report for
Bug fixes - 19/11/2026 - 02, section 10.

1. Content-source kinds were shown with `replace("_", " ") | title`, so the
   `dlc` kind rendered as "Dlc" (and `user` as "User"). One shared label map and
   Jinja filter now serve every screen and the Add/Edit form.
2. The campaign dashboard's Progression section still said "Catalog and planning
   are coming in Phase 3-5" although research, the Avenger, soldiers and builds
   all exist. It now shows real, per-campaign numbers taken from the existing
   services, linking to those screens.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models.catalog import (
    CONTENT_SOURCE_KIND_LABELS,
    CONTENT_SOURCE_KINDS,
    content_source_kind_label,
)
from app.repositories import content_sources as sources_repo
from app.services import campaign_lifecycle
from app.services.campaign_overview import progression_summary
from app.services.content_sources import create_content_source
from tests.avenger_helpers import import_real_packs

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


def new_campaign(name, profile="wotc-standard"):
    return campaign_lifecycle.create_campaign(name=name, game_profile_slug=profile)


# ===================================================== 1. content-source kind labels


def test_every_kind_has_an_explicit_human_label():
    assert set(CONTENT_SOURCE_KIND_LABELS) == set(CONTENT_SOURCE_KINDS)  # a new kind cannot be forgotten
    assert [content_source_kind_label(k) for k in CONTENT_SOURCE_KINDS] == [
        "Base game", "DLC", "Total conversion", "Workshop mod", "Custom",
    ]  # fmt: skip
    assert "Dlc" not in CONTENT_SOURCE_KIND_LABELS.values()


def test_label_helper_handles_unknown_and_missing_kinds_gracefully():
    assert content_source_kind_label("some_future_kind") == "Some Future Kind"
    assert content_source_kind_label("") == "" and content_source_kind_label(None) == ""


def test_the_jinja_filter_is_registered_and_used(app):
    assert app.jinja_env.filters["source_kind"]("dlc") == "DLC"


def test_no_template_formats_a_source_kind_with_title_case_any_more():
    offenders = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\.kind\b[^}]*\|\s*title", line):
                offenders.append(f"{path.relative_to(TEMPLATES)}:{number}")
    assert not offenders, "use `| source_kind` instead of title-casing: " + ", ".join(offenders)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


@pytest.fixture()
def mods(app, seeded_db):
    return {
        "workshop": create_content_source(slug="wp", name="Weapon Pack", kind="workshop_mod"),
        "custom": create_content_source(slug="mine", name="My Stuff", kind="user"),
        "total": create_content_source(slug="tc", name="Big Overhaul", kind="total_conversion"),
    }


def test_every_screen_that_shows_a_kind_uses_the_proper_label(client, app, mods):
    campaign = new_campaign("Labels", "wotc-standard")
    wotc = sources_repo.get_content_source_by_slug("wotc")
    pages = {
        "dashboard": f"/campaigns/{campaign.id}/dashboard",
        "campaign sources": f"/campaigns/{campaign.id}/sources",
        "source list": "/content/sources/",
        "source detail (dlc)": f"/content/sources/{wotc.id}",
        "source detail (workshop)": f"/content/sources/{mods['workshop'].id}",
    }
    for name, url in pages.items():
        response = client.get(url)
        assert response.status_code == 200, name
        text = _text(response.get_data(as_text=True))
        assert not re.search(r"\bDlc\b", text), f"{name} still shows 'Dlc'"
        assert not re.search(r"\bWorkshop Mod\b|\bTotal Conversion\b", text), f"{name} still title-cases"

    assert "DLC" in _text(client.get(pages["dashboard"]).get_data(as_text=True))
    assert "Base game" in _text(client.get(pages["campaign sources"]).get_data(as_text=True))
    listing = _text(client.get("/content/sources/").get_data(as_text=True))
    assert "Workshop mod" in listing and "Total conversion" in listing and "Custom" in listing
    assert "DLC" in _text(client.get(pages["source detail (dlc)"]).get_data(as_text=True))


def test_the_add_source_form_offers_the_proper_labels_but_submits_the_stored_values(client, app, seeded_db):
    html = client.get("/content/sources/new").get_data(as_text=True)
    options = dict(re.findall(r'<option[^>]*value="([^"]+)"[^>]*>([^<]+)</option>', html))
    assert options == {k: content_source_kind_label(k) for k in CONTENT_SOURCE_KINDS}
    assert options["dlc"] == "DLC"

    response = client.post("/content/sources/new", data={"name": "A DLC Pack", "kind": "dlc", "version": "1.0"})
    assert response.status_code == 302
    created = next(s for s in sources_repo.list_content_sources() if s.name == "A DLC Pack")
    assert created.kind == "dlc"  # the stored value is unchanged; only the label differs


# ===================================================== 2. dashboard progression


@pytest.fixture()
def real(app, seeded_db):
    with app.app_context():
        import_real_packs()
        yield


def card_text(html: str, card: str) -> str:
    match = re.search(rf'<a class="card[^"]*"[^>]*data-card="{card}".*?</a>', html, re.DOTALL)
    assert match, f"no {card} card on the dashboard"
    return _text(match.group(0)).strip()


def oid(key):
    from app.models import CatalogObject

    return CatalogObject.query.filter_by(template_key=key).first().id


def test_dashboard_no_longer_claims_the_features_are_unbuilt(client, real):
    campaign = new_campaign("Fresh")
    html = client.get(f"/campaigns/{campaign.id}/dashboard").get_data(as_text=True)
    text = _text(html)

    assert "coming in Phase" not in text and "Phase 3" not in text and "implemented" not in text
    assert 'data-testid="progression"' in html


def test_progression_cards_link_to_this_campaigns_own_screens(client, real):
    campaign = new_campaign("Links")
    html = client.get(f"/campaigns/{campaign.id}/dashboard").get_data(as_text=True)
    cid = campaign.id
    for card, href in (
        ("research", f"/campaigns/{cid}/research/"), ("avenger", f"/campaigns/{cid}/avenger/"),
        ("soldiers", f"/campaigns/{cid}/soldiers/"), ("builds", f"/campaigns/{cid}/builds/"),
    ):  # fmt: skip
        match = re.search(rf'<a class="card[^"]*" href="([^"]+)"[^>]*data-card="{card}"', html)
        assert match and match.group(1) == href, card
        assert client.get(href).status_code == 200


def test_progression_starts_at_zero_for_a_new_campaign_and_total_matches_the_catalog(real):
    campaign = new_campaign("Zeroes")
    summary = progression_summary(campaign)
    assert (summary.research_completed, summary.research_in_progress, summary.research_planned) == (0, 0, 0)
    assert (summary.facilities_built, summary.facilities_constructing, summary.facilities_planned) == (0, 0, 0)
    assert (summary.soldiers, summary.builds) == (0, 0)
    assert summary.research_total == 20 and summary.research_available >= 1  # WotC campaign: base + WotC projects


def test_progression_reflects_real_progress_across_all_four_areas(client, real):
    from app.services import avenger, builds, research, soldiers

    campaign = new_campaign("Busy")
    research.complete_research(campaign, oid("AlienBiotech"))
    research.complete_research(campaign, oid("HybridMaterials"))
    research.start_research(campaign, oid("ModularWeapons"))
    research.add_to_queue(campaign, oid("MagnetizedWeapons"))
    lab = next(o.object_id for o in avenger.list_facility_options(campaign) if o.name == "Laboratory")
    workshop = next(o.object_id for o in avenger.list_facility_options(campaign) if o.name == "Workshop")
    avenger.assign_facility(campaign, "R1C1", lab, build_state="built")
    avenger.assign_facility(campaign, "R1C2", workshop, build_state="constructing")
    soldiers.create_soldier(campaign, name="Jane")
    soldiers.create_soldier(campaign, name="Tom")
    builds.create_build(campaign, "weapon", base_object_id=oid("Shotgun_CV"), name="B1")

    summary = progression_summary(campaign)
    assert (summary.research_completed, summary.research_in_progress, summary.research_planned) == (2, 1, 1)
    assert (summary.facilities_built, summary.facilities_constructing, summary.facilities_planned) == (1, 1, 0)
    assert (summary.soldiers, summary.builds) == (2, 1)

    html = client.get(f"/campaigns/{campaign.id}/dashboard").get_data(as_text=True)
    assert card_text(html, "research").startswith("Research 2 of 20 completed")
    assert "1 in progress" in card_text(html, "research") and "1 planned" in card_text(html, "research")
    avenger_card = card_text(html, "avenger")
    assert "1 facility built" in avenger_card and "1 constructing" in avenger_card and "0 planned" in avenger_card
    assert "2 on the roster" in card_text(html, "soldiers")
    assert "1 saved build" in card_text(html, "builds")


def test_each_campaigns_dashboard_shows_only_its_own_progress(client, real):
    from app.services import research, soldiers

    a, b = new_campaign("Campaign A"), new_campaign("Campaign B")
    research.complete_research(a, oid("AlienBiotech"))
    soldiers.create_soldier(a, name="Only In A")

    html_a = client.get(f"/campaigns/{a.id}/dashboard").get_data(as_text=True)
    html_b = client.get(f"/campaigns/{b.id}/dashboard").get_data(as_text=True)

    assert "1 of 20 completed" in card_text(html_a, "research") and "1 on the roster" in card_text(html_a, "soldiers")
    assert "0 of 20 completed" in card_text(html_b, "research") and "0 on the roster" in card_text(html_b, "soldiers")


def test_progression_follows_the_campaigns_sources(client, real):
    wotc, lwotc = new_campaign("Standard", "wotc-standard"), new_campaign("Long War", "lwotc")
    assert progression_summary(lwotc).research_total > progression_summary(wotc).research_total  # LWotC adds projects


def test_a_campaign_with_no_sources_still_renders_the_progression_section(client, real):
    campaign = new_campaign("Bare", None)
    response = client.get(f"/campaigns/{campaign.id}/dashboard")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "No content sources configured" in html and 'data-testid="progression"' in html
    assert progression_summary(campaign).research_total == 0


def test_viewing_the_dashboard_changes_nothing(client, real):
    from app.extensions import db
    from app.models import CampaignAvengerCell, CampaignObjectState, CampaignResearchQueueItem

    campaign = new_campaign("Read only")
    snapshot = lambda: (
        db.session.query(CampaignObjectState).count(), db.session.query(CampaignResearchQueueItem).count(),
        [(c.coordinate, c.build_state, c.facility_object_id) for c in CampaignAvengerCell.query.filter_by(campaign_id=campaign.id).order_by(CampaignAvengerCell.coordinate)],
        campaign.effective_catalog_version,
    )  # fmt: skip
    before = snapshot()
    for _ in range(2):
        client.get(f"/campaigns/{campaign.id}/dashboard")
    db.session.expire_all()
    assert snapshot() == before
