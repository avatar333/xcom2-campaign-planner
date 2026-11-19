"""Regression tests for Bug fixes - 19/11/2026 - 02.

Root cause: `dashboard/campaign_dashboard.html` looped over
`campaign.content_sources` (the association rows) and printed
`Source #{{ cs.content_source_id }}` -- the raw foreign-key id -- because it
never resolved the `ContentSource` each association points at. The
Campaigns -> Sources screen resolved the real source for every association, so
it showed names. Both read the same persisted rows; only the display differed.

Fix: `app.services.campaign_sources.list_campaign_sources` is now the single
place that turns a campaign's persisted associations into named sources (in
explicit precedence order), and both screens use it.

The page-level assertions read the template context captured through Flask's
`template_rendered` signal (the view model each screen was actually given) as
well as the rendered table, so they do not depend on incidental HTML.
"""
from __future__ import annotations

import re
from contextlib import contextmanager

from flask import template_rendered

from app.repositories import content_sources as sources_repo
from app.services import campaign_lifecycle
from app.services import campaign_sources as campaign_sources_service
from app.services.campaign_sources import list_campaign_sources
from app.services.content_sources import create_content_source

MOD_NAME = "[WOTC] Example Weapon Pack"


@contextmanager
def captured_contexts(app):
    """Collect (template name, context) for every template rendered inside."""
    seen: list[tuple[str, dict]] = []

    def record(sender, template, context, **extra):
        seen.append((template.name, dict(context)))

    template_rendered.connect(record, app)
    try:
        yield seen
    finally:
        template_rendered.disconnect(record, app)


def dashboard_url(campaign):
    return f"/campaigns/{campaign.id}/dashboard"


def sources_url(campaign, suffix=""):
    return f"/campaigns/{campaign.id}/sources{suffix}"


def sources_table(html: str) -> str:
    start = html.index('data-testid="campaign-sources"')
    return html[start : html.index("</table>", start)]


def dashboard_source_names(html: str) -> list[str]:
    """Source names in the dashboard table, in displayed order."""
    return re.findall(r"<td><a [^>]*>([^<]+)</a></td>", sources_table(html))


def new_campaign(name, profile="wotc-standard"):
    return campaign_lifecycle.create_campaign(name=name, game_profile_slug=profile)


def attach(campaign, source_or_slug):
    source = sources_repo.get_content_source_by_slug(source_or_slug) if isinstance(source_or_slug, str) else source_or_slug
    campaign_sources_service.add_source_to_campaign(campaign, source)
    return source


def make_mod(slug="example-weapon-pack", name=MOD_NAME):
    return create_content_source(slug=slug, name=name, kind="workshop_mod")


# ---------------------------------------------------------------- the bug


def test_dashboard_shows_the_real_source_names_not_database_ids(app, client, seeded_db):
    campaign = new_campaign("LWOTC Campaign", "lwotc")  # XCOM 2, War of the Chosen, Long War of the Chosen
    html = client.get(dashboard_url(campaign)).get_data(as_text=True)

    assert dashboard_source_names(html) == ["XCOM 2", "War of the Chosen", "Long War of the Chosen"]
    assert "Source #" not in html  # the old placeholder
    assert not re.search(r"<td>\s*\d+\s*</td>\s*<td>\s*\d+", sources_table(html)), "an id must never stand in for a name"


def test_dashboard_names_are_the_stored_names_not_derived_from_the_profile(app, client, seeded_db):
    """Rename a source; the dashboard must follow the database, proving the text
    is read from the persisted association rather than built from the profile."""
    campaign = new_campaign("Renamed", "wotc-standard")
    wotc = sources_repo.get_content_source_by_slug("wotc")
    sources_repo.update_content_source(wotc, name="War of the Chosen (renamed in DB)")

    html = client.get(dashboard_url(campaign)).get_data(as_text=True)

    assert "War of the Chosen (renamed in DB)" in dashboard_source_names(html)


def test_dashboard_shows_kind_precedence_and_status_alongside_the_name(app, client, seeded_db):
    campaign = new_campaign("Detail", "wotc-standard")
    html = client.get(dashboard_url(campaign)).get_data(as_text=True)
    rows = re.findall(r"<tr>\s*<td><a [^>]*>([^<]+)</a></td>\s*<td>([^<]*)</td>\s*<td>(\d+)</td>\s*<td>.*?(Enabled|Disabled).*?</td>", sources_table(html), re.DOTALL)
    assert rows == [("XCOM 2", "Base game", "0", "Enabled"), ("War of the Chosen", "DLC", "1", "Enabled")]


# --------------------------------------------------------- shared source of truth


def test_dashboard_and_sources_screens_are_given_the_same_associations(app, client, seeded_db):
    campaign = new_campaign("Consistency", "lwotc")
    attach(campaign, make_mod())

    with captured_contexts(app) as seen:
        dashboard_html = client.get(dashboard_url(campaign)).get_data(as_text=True)
        sources_html = client.get(sources_url(campaign)).get_data(as_text=True)

    dashboard_ctx = next(c for n, c in seen if n == "dashboard/campaign_dashboard.html")
    sources_ctx = next(c for n, c in seen if n == "campaigns/sources.html")
    on_dashboard = [(v.association.id, v.source.id, v.name) for v in dashboard_ctx["sources"]]
    on_sources = [(assoc.id, source.id, source.name) for assoc, source in sources_ctx["attached_sources"]]
    assert on_dashboard == on_sources  # same rows, same order, same names
    assert [name for _a, _s, name in on_dashboard] == ["XCOM 2", "War of the Chosen", "Long War of the Chosen", MOD_NAME]

    shown_on_sources_page = [n for n in re.findall(r"<td>\s*([^<]+?)\s*(?:<span|</td>)", sources_html) if n in {x[2] for x in on_dashboard}]
    assert shown_on_sources_page == dashboard_source_names(dashboard_html)


def test_both_screens_match_the_raw_database_rows(app, client, seeded_db):
    from app.extensions import db
    from app.models import CampaignContentSource, ContentSource

    campaign = new_campaign("Truth", "lwotc")
    attach(campaign, make_mod())
    rows = (
        db.session.query(ContentSource.name)
        .join(CampaignContentSource, CampaignContentSource.content_source_id == ContentSource.id)
        .filter(CampaignContentSource.campaign_id == campaign.id)
        .order_by(CampaignContentSource.precedence, CampaignContentSource.id)
        .all()
    )
    assert [r[0] for r in rows] == [v.name for v in list_campaign_sources(campaign.id)]
    assert dashboard_source_names(client.get(dashboard_url(campaign)).get_data(as_text=True)) == [r[0] for r in rows]


# ------------------------------------------------------------------ custom / mod


def test_a_custom_mod_source_appears_on_the_dashboard(app, client, seeded_db):
    campaign = new_campaign("Modded", "wotc-standard")
    attach(campaign, make_mod())
    html = client.get(dashboard_url(campaign)).get_data(as_text=True)
    assert dashboard_source_names(html) == ["XCOM 2", "War of the Chosen", MOD_NAME]


def test_a_source_attached_through_the_ui_route_appears_on_both_screens(app, client, seeded_db):
    campaign = new_campaign("Via UI", "wotc-standard")
    mod = make_mod()
    response = client.post(sources_url(campaign, "/add"), data={"content_source_id": str(mod.id)})
    assert response.status_code == 302

    assert MOD_NAME in dashboard_source_names(client.get(dashboard_url(campaign)).get_data(as_text=True))
    assert MOD_NAME in client.get(sources_url(campaign)).get_data(as_text=True)
    # reload: still there
    assert MOD_NAME in dashboard_source_names(client.get(dashboard_url(campaign)).get_data(as_text=True))


def test_dashboard_is_not_derived_from_the_profile(app, client, seeded_db):
    """A 'lwotc' campaign whose Long War source was later removed must show what
    is actually attached, not what the profile would suggest."""
    campaign = new_campaign("Profile says LWOTC", "lwotc")
    lwotc = sources_repo.get_content_source_by_slug("lwotc")
    assert client.post(sources_url(campaign, f"/{lwotc.id}/remove")).status_code == 302

    assert campaign.game_profile_slug == "lwotc"
    assert dashboard_source_names(client.get(dashboard_url(campaign)).get_data(as_text=True)) == ["XCOM 2", "War of the Chosen"]


def test_a_campaign_created_without_a_profile_shows_its_added_sources(app, client, seeded_db):
    campaign = new_campaign("Custom setup", None)
    attach(campaign, "xcom2-base")
    attach(campaign, make_mod())
    html = client.get(dashboard_url(campaign)).get_data(as_text=True)
    assert dashboard_source_names(html) == ["XCOM 2", MOD_NAME]


# ------------------------------------------------------------- removal / disable


def test_removing_a_source_updates_the_dashboard(app, client, seeded_db):
    campaign = new_campaign("Removal", "lwotc")
    wotc = sources_repo.get_content_source_by_slug("wotc")
    assert "War of the Chosen" in dashboard_source_names(client.get(dashboard_url(campaign)).get_data(as_text=True))

    assert client.post(sources_url(campaign, f"/{wotc.id}/remove")).status_code == 302

    names = dashboard_source_names(client.get(dashboard_url(campaign)).get_data(as_text=True))
    assert names == ["XCOM 2", "Long War of the Chosen"]


def test_disabling_a_source_shows_it_as_disabled_and_toggling_back_restores_it(app, client, seeded_db):
    campaign = new_campaign("Toggle", "wotc-standard")
    wotc = sources_repo.get_content_source_by_slug("wotc")

    client.post(sources_url(campaign, f"/{wotc.id}/toggle"))
    html = client.get(dashboard_url(campaign)).get_data(as_text=True)
    row = re.search(r"<tr>\s*<td><a [^>]*>War of the Chosen</a>.*?</tr>", sources_table(html), re.DOTALL).group(0)
    assert "Disabled" in row and "badge--unknown" in row
    assert dashboard_source_names(html) == ["XCOM 2", "War of the Chosen"]  # still listed, clearly marked
    assert "1 enabled of 2 attached" in html

    client.post(sources_url(campaign, f"/{wotc.id}/toggle"))
    html = client.get(dashboard_url(campaign)).get_data(as_text=True)
    assert "2 enabled of 2 attached" in html and "Disabled" not in sources_table(html)


# ----------------------------------------------------------------- empty state


def test_a_campaign_with_no_sources_shows_a_proper_empty_state(app, client, seeded_db):
    campaign = new_campaign("Bare", None)
    html = client.get(dashboard_url(campaign)).get_data(as_text=True)

    section = html[html.index("Content Sources") : html.index("Progression")] if "Progression" in html else html
    assert "No content sources configured" in html
    assert 'data-testid="campaign-sources"' not in html
    assert "Source #" not in html
    assert "arrives in Phase 3" not in section and "No content sources enabled" not in html  # the old stale wording
    assert f'href="/campaigns/{campaign.id}/sources"' in html  # and a way to fix it


# -------------------------------------------------------------------- ordering


def test_order_follows_precedence_and_reordering(app, client, seeded_db):
    campaign = new_campaign("Order", "wotc-standard")
    mod = attach(campaign, make_mod())
    base, wotc = (sources_repo.get_content_source_by_slug(s) for s in ("xcom2-base", "wotc"))
    assert dashboard_source_names(client.get(dashboard_url(campaign)).get_data(as_text=True)) == ["XCOM 2", "War of the Chosen", MOD_NAME]

    campaign_sources_service.reorder_sources(campaign, [mod.id, base.id, wotc.id])

    html = client.get(dashboard_url(campaign)).get_data(as_text=True)
    assert dashboard_source_names(html) == [MOD_NAME, "XCOM 2", "War of the Chosen"]
    assert re.findall(r"<td>(\d+)</td>\s*<td>\s*<span", sources_table(html)) == ["0", "1", "2"]


def test_equal_precedence_has_a_stable_order(app, client, seeded_db):
    from app.extensions import db
    from app.models import CampaignContentSource

    campaign = new_campaign("Ties", "wotc-standard")
    for assoc in CampaignContentSource.query.filter_by(campaign_id=campaign.id).all():
        assoc.precedence = 5
    db.session.commit()

    first = [v.name for v in list_campaign_sources(campaign.id)]
    assert first == ["XCOM 2", "War of the Chosen"]  # association id breaks the tie
    assert all([v.name for v in list_campaign_sources(campaign.id)] == first for _ in range(3))


def test_source_precedence_and_overrides_are_untouched_by_viewing(app, client, seeded_db):
    from app.services import effective_catalog

    campaign = new_campaign("Untouched", "lwotc")
    before = [(v.association.id, v.precedence, v.enabled) for v in list_campaign_sources(campaign.id)]
    version = campaign.effective_catalog_version
    order = effective_catalog.effective_source_ids(campaign.id)

    for _ in range(2):
        client.get(dashboard_url(campaign))
        client.get(sources_url(campaign))

    assert [(v.association.id, v.precedence, v.enabled) for v in list_campaign_sources(campaign.id)] == before
    assert campaign.effective_catalog_version == version  # viewing never invalidates or recreates anything
    assert effective_catalog.effective_source_ids(campaign.id) == order


# ------------------------------------------------------------------- isolation


def test_each_campaign_shows_only_its_own_sources(app, client, seeded_db):
    a = new_campaign("Campaign A", "wotc-standard")  # XCOM 2, WOTC
    b = new_campaign("Campaign B", "lwotc")  # XCOM 2, WOTC, LWOTC
    attach(b, make_mod())  # + custom mod

    names_a = dashboard_source_names(client.get(dashboard_url(a)).get_data(as_text=True))
    names_b = dashboard_source_names(client.get(dashboard_url(b)).get_data(as_text=True))

    assert names_a == ["XCOM 2", "War of the Chosen"]
    assert names_b == ["XCOM 2", "War of the Chosen", "Long War of the Chosen", MOD_NAME]
    assert "Long War of the Chosen" not in names_a and MOD_NAME not in names_a


def test_changing_one_campaigns_sources_does_not_change_another(app, client, seeded_db):
    a, b = new_campaign("A", "wotc-standard"), new_campaign("B", "wotc-standard")
    wotc = sources_repo.get_content_source_by_slug("wotc")
    client.post(sources_url(a, f"/{wotc.id}/remove"))
    attach(b, make_mod())

    assert dashboard_source_names(client.get(dashboard_url(a)).get_data(as_text=True)) == ["XCOM 2"]
    assert dashboard_source_names(client.get(dashboard_url(b)).get_data(as_text=True)) == ["XCOM 2", "War of the Chosen", MOD_NAME]


def test_dashboard_for_a_missing_campaign_is_404(client, seeded_db):
    assert client.get("/campaigns/99999/dashboard").status_code == 404


# ------------------------------------------------------------- the shared helper


def test_helper_returns_named_views_in_precedence_order_for_one_campaign_only(app, seeded_db):
    a, b = new_campaign("A", "lwotc"), new_campaign("B", "wotc-standard")
    views = list_campaign_sources(a.id)
    assert [(v.name, v.slug, v.kind, v.precedence, v.enabled) for v in views] == [
        ("XCOM 2", "xcom2-base", "base", 0, True),
        ("War of the Chosen", "wotc", "dlc", 1, True),
        ("Long War of the Chosen", "lwotc", "total_conversion", 2, True),
    ]
    assert all(v.association.campaign_id == a.id for v in views)
    assert [v.name for v in list_campaign_sources(b.id)] == ["XCOM 2", "War of the Chosen"]
    assert list_campaign_sources(999999) == []


def test_helper_resolves_names_without_a_query_per_source(app, seeded_db):
    """The names come from one joined load, not one lookup per association."""
    from sqlalchemy import event

    from app.extensions import db

    campaign = new_campaign("Queries", "lwotc")
    attach(campaign, make_mod())
    campaign_id = campaign.id
    db.session.expire_all()
    statements = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", count)
    try:
        names = [v.name for v in list_campaign_sources(campaign_id)]
    finally:
        event.remove(db.engine, "before_cursor_execute", count)

    assert len(names) == 4
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 1
