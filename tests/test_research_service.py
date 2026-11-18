"""Research & Dependency Planner service tests (ADR-0021): per-campaign
state, queue ordering/validation, recursive traversal over *catalog data*
(real curated packs and synthetic packs), ruleset-specific dependencies."""
from __future__ import annotations

import pytest

from tests.avenger_helpers import (
    attach_source,
    import_real_packs,
    new_campaign,
    write_pack,
)


@pytest.fixture()
def real(app, seeded_db):
    with app.app_context():
        import_real_packs()
        yield


def oid(template_key: str) -> int:
    from app.models import CatalogObject

    return CatalogObject.query.filter_by(template_key=template_key).first().id


def names(refs) -> list[str]:
    return [r.name for r in refs]


# ------------------------------------------------------------ states


def test_fresh_campaign_has_only_root_projects_available(app, real):
    from app.services import research

    browse = research.browse_research(new_campaign("A"))
    status = {c.ref.name: c.ref.status for c in browse.cards}

    assert status["Alien Biotech"] == "available" and status["Hybrid Materials"] == "available"
    assert status["Magnetic Weapons"] == "blocked"
    assert browse.counts["completed"] == 0 and browse.counts["planned"] == 0


def test_completion_unlocks_dependents_and_records_order(app, real):
    from app.services import campaign_progress, research

    campaign = new_campaign("A")
    research.complete_research(campaign, oid("AlienBiotech"))
    research.complete_research(campaign, oid("HybridMaterials"))

    detail = research.get_research_detail(campaign, oid("AlienBiotech"))
    assert detail.ref.status == "completed" and detail.completed_at is not None
    assert detail.completion_sequence == 1
    assert research.get_research_detail(campaign, oid("HybridMaterials")).completion_sequence == 2
    sectoid = research.get_research_detail(campaign, oid("AutopsySectoid"))
    assert sectoid.ref.status == "available" and sectoid.can_start

    research.complete_research(campaign, oid("AlienBiotech"))  # idempotent: keeps first timestamp/order
    assert research.get_research_detail(campaign, oid("AlienBiotech")).completion_sequence == 1
    assert campaign_progress.get_state(campaign, oid("AlienBiotech")).status == "completed"


def test_reopen_clears_completion(app, real):
    from app.services import research

    campaign = new_campaign("A")
    research.complete_research(campaign, oid("AlienBiotech"))
    research.reopen_research(campaign, oid("AlienBiotech"))

    detail = research.get_research_detail(campaign, oid("AlienBiotech"))
    assert detail.ref.status == "available" and detail.completed_at is None and detail.completion_sequence is None


def test_planned_and_in_progress_states(app, real):
    from app.services import research

    campaign = new_campaign("A")
    research.add_to_queue(campaign, oid("AlienBiotech"))
    assert research.get_research_detail(campaign, oid("AlienBiotech")).ref.status == "planned"
    research.start_research(campaign, oid("AlienBiotech"))
    assert research.get_research_detail(campaign, oid("AlienBiotech")).ref.status == "in_progress"
    assert [i.state for i in research.get_queue_view(campaign).items] == ["in_progress"]
    research.complete_research(campaign, oid("AlienBiotech"))
    assert research.get_queue_view(campaign).items == ()  # completion leaves the queue


def test_completed_dependency_does_not_leak_into_catalog_definitions(app, real):
    from app.models import CatalogObject
    from app.services import research

    before = {o.id: (o.name, o.attributes, o.tier, o.is_retired) for o in CatalogObject.query.all()}
    campaign = new_campaign("A")
    research.complete_research(campaign, oid("AlienBiotech"))
    research.add_to_queue(campaign, oid("HybridMaterials"))
    after = {o.id: (o.name, o.attributes, o.tier, o.is_retired) for o in CatalogObject.query.all()}
    assert before == after  # global research definitions are never mutated by progression


# --------------------------------------------------------- isolation


def test_research_state_and_queue_are_independent_per_campaign(app, real):
    from app.services import research

    a, b = new_campaign("A"), new_campaign("B")
    research.complete_research(a, oid("AlienBiotech"))
    research.add_to_queue(a, oid("HybridMaterials"))
    research.add_to_queue(b, oid("ModularWeapons"))

    assert research.get_research_detail(a, oid("AlienBiotech")).ref.status == "completed"
    assert research.get_research_detail(b, oid("AlienBiotech")).ref.status == "available"
    assert [i.ref.name for i in research.get_queue_view(a).items] == ["Hybrid Materials"]
    assert [i.ref.name for i in research.get_queue_view(b).items] == ["Modular Weapons"]
    # B's unlock path knows nothing about A's progress
    assert not research.get_unlock_path(b, oid("AutopsySectoid")).satisfied
    assert names(research.get_unlock_path(a, oid("AutopsySectoid")).satisfied) == ["Alien Biotech"]


def test_deleting_a_campaign_leaves_the_other_campaigns_research(app, real):
    from app.models import CampaignResearchQueueItem
    from app.services import campaign_lifecycle, research

    a, b = new_campaign("A"), new_campaign("B")
    research.complete_research(a, oid("AlienBiotech"))
    research.add_to_queue(a, oid("HybridMaterials"))
    research.complete_research(b, oid("HybridMaterials"))
    research.add_to_queue(b, oid("ModularWeapons"))
    a_id, b_id = a.id, b.id

    campaign_lifecycle.delete_campaign(a)

    assert CampaignResearchQueueItem.query.filter_by(campaign_id=a_id).count() == 0
    assert CampaignResearchQueueItem.query.filter_by(campaign_id=b_id).count() == 1
    assert research.get_research_detail(b, oid("HybridMaterials")).ref.status == "completed"


# ------------------------------------------------------------- queue


def _queue_names(campaign):
    from app.services import research

    return [i.ref.name for i in research.get_queue_view(campaign).items]


def test_queue_order_is_persisted_and_renumbered(app, real):
    from app.models import CampaignResearchQueueItem
    from app.services import research

    campaign = new_campaign("A")
    for key in ("AlienBiotech", "HybridMaterials", "ModularWeapons"):
        research.add_to_queue(campaign, oid(key))
    assert _queue_names(campaign) == ["Alien Biotech", "Hybrid Materials", "Modular Weapons"]

    research.reorder_queue(campaign, [oid("ModularWeapons"), oid("AlienBiotech"), oid("HybridMaterials")])
    assert _queue_names(campaign) == ["Modular Weapons", "Alien Biotech", "Hybrid Materials"]
    positions = [r.position for r in CampaignResearchQueueItem.query.filter_by(campaign_id=campaign.id).order_by(CampaignResearchQueueItem.position)]
    assert positions == [0, 1, 2]

    research.remove_from_queue(campaign, oid("AlienBiotech"))
    assert _queue_names(campaign) == ["Modular Weapons", "Hybrid Materials"]
    assert [r.position for r in CampaignResearchQueueItem.query.filter_by(campaign_id=campaign.id).order_by(CampaignResearchQueueItem.position)] == [0, 1]


def test_add_at_position_and_move_helpers(app, real):
    from app.services import research

    campaign = new_campaign("A")
    for key in ("AlienBiotech", "HybridMaterials"):
        research.add_to_queue(campaign, oid(key))
    research.add_to_queue(campaign, oid("ModularWeapons"), position=1)
    assert _queue_names(campaign) == ["Modular Weapons", "Alien Biotech", "Hybrid Materials"]

    research.move_in_queue(campaign, oid("HybridMaterials"), offset=-1)
    assert _queue_names(campaign) == ["Modular Weapons", "Hybrid Materials", "Alien Biotech"]
    research.move_in_queue(campaign, oid("Alien" + "Biotech"), to_position=1)
    assert _queue_names(campaign) == ["Alien Biotech", "Modular Weapons", "Hybrid Materials"]
    research.move_in_queue(campaign, oid("AlienBiotech"), offset=-5)  # clamps
    assert _queue_names(campaign)[0] == "Alien Biotech"
    research.move_in_queue(campaign, oid("AlienBiotech"), to_position=99)
    assert _queue_names(campaign)[-1] == "Alien Biotech"


def test_reorder_rejects_partial_duplicate_or_foreign_lists(app, real):
    from app.services import research

    campaign = new_campaign("A")
    for key in ("AlienBiotech", "HybridMaterials"):
        research.add_to_queue(campaign, oid(key))
    for bad in ([oid("AlienBiotech")], [oid("AlienBiotech")] * 2, [oid("AlienBiotech"), oid("ModularWeapons")], []):
        with pytest.raises(research.ResearchError):
            research.reorder_queue(campaign, bad)
    assert _queue_names(campaign) == ["Alien Biotech", "Hybrid Materials"]  # unchanged


def test_queue_rejects_invalid_additions(app, real):
    from app.services import research

    campaign = new_campaign("A")
    research.add_to_queue(campaign, oid("AlienBiotech"))
    with pytest.raises(research.ResearchError, match="already in the queue"):
        research.add_to_queue(campaign, oid("AlienBiotech"))
    with pytest.raises(research.ResearchError, match="Only research"):
        research.add_to_queue(campaign, oid("Laboratory"))  # a facility is not a research project
    with pytest.raises(research.ResearchError):
        research.add_to_queue(campaign, 987654)
    research.complete_research(campaign, oid("HybridMaterials"))
    with pytest.raises(research.ResearchError, match="already completed"):
        research.add_to_queue(campaign, oid("HybridMaterials"))
    with pytest.raises(research.ResearchError):
        research.remove_from_queue(campaign, oid("ModularWeapons"))


def test_project_outside_the_campaigns_sources_cannot_be_queued(app, real):
    from app.services import research

    wotc = new_campaign("WotC", "wotc-standard")
    with pytest.raises(research.ResearchError):
        research.add_to_queue(wotc, oid("LaserWeapons"))  # LWotC-only project


def test_removing_an_in_progress_project_stops_it(app, real):
    from app.services import research

    campaign = new_campaign("A")
    research.start_research(campaign, oid("AlienBiotech"))
    research.remove_from_queue(campaign, oid("AlienBiotech"))
    assert research.get_research_detail(campaign, oid("AlienBiotech")).ref.status == "available"


# -------------------------------------------------- queue validation


def test_impossible_order_warns_instead_of_blocking(app, real):
    from app.services import research

    campaign = new_campaign("LWotC", "lwotc")
    research.add_to_queue(campaign, oid("PlasmaRifle_LWOTC"))  # allowed even though impossible now
    view = research.get_queue_view(campaign)

    assert [i.ref.name for i in view.items] == ["Plasma Rifle"] and view.warnings_total == 1
    text = " ".join(w.text for w in view.items[0].warnings)
    assert "Advanced Coilguns" in text and "Advanced Laser Weapons" in text


def test_prerequisite_queued_earlier_clears_the_warning(app, real):
    from app.services import research

    campaign = new_campaign("A")
    research.add_to_queue(campaign, oid("HybridMaterials"))
    research.add_to_queue(campaign, oid("ModularWeapons"))
    research.add_to_queue(campaign, oid("AutopsyAdventOfficer"))
    assert research.get_queue_view(campaign).items[2].warnings  # needs Alien Biotech, not queued yet

    research.add_to_queue(campaign, oid("AlienBiotech"), position=1)
    research.add_to_queue(campaign, oid("MagnetizedWeapons"))
    view = research.get_queue_view(campaign)
    assert [i.ref.name for i in view.items] == [
        "Alien Biotech", "Hybrid Materials", "Modular Weapons", "ADVENT Officer Autopsy", "Magnetic Weapons"
    ]  # fmt: skip
    assert view.warnings_total == 0


def test_dependent_queued_before_its_prerequisite_warns_with_position(app, real):
    from app.services import research

    campaign = new_campaign("A")
    research.add_to_queue(campaign, oid("AutopsySectoid"))
    research.add_to_queue(campaign, oid("AlienBiotech"))
    view = research.get_queue_view(campaign)

    assert view.items[0].warnings and "Alien Biotech incomplete" in view.items[0].warnings[0].text
    assert not view.items[1].warnings
    assert view.items[0].position == 1


def test_in_progress_prerequisite_does_not_count_as_done(app, real):
    from app.services import research

    campaign = new_campaign("A")
    research.start_research(campaign, oid("AlienBiotech"))
    research.add_to_queue(campaign, oid("AutopsySectoid"))
    view = research.get_queue_view(campaign)
    # Alien Biotech is ahead of it in the queue, so by that position it will be complete.
    assert [i.ref.name for i in view.items] == ["Alien Biotech", "Sectoid Autopsy"]
    assert view.warnings_total == 0
    research.move_in_queue(campaign, oid("AutopsySectoid"), to_position=1)
    assert research.get_queue_view(campaign).warnings_total == 1


def test_suggested_order_is_stable_and_never_applied_automatically(app, real):
    from app.services import research

    campaign = new_campaign("A")
    for key in ("MagnetizedWeapons", "AutopsyAdventOfficer", "ModularWeapons", "HybridMaterials", "AlienBiotech"):
        research.add_to_queue(campaign, oid(key))
    before = _queue_names(campaign)

    suggestion = research.suggest_queue_order(campaign)
    assert suggestion.changed
    order = [research.get_research_detail(campaign, o).ref.name for o in suggestion.order]
    assert order.index("Alien Biotech") < order.index("ADVENT Officer Autopsy") < order.index("Magnetic Weapons")
    assert order.index("Hybrid Materials") < order.index("Magnetic Weapons")
    assert order.index("Modular Weapons") < order.index("Magnetic Weapons")
    assert sorted(order) == sorted(before)  # the same projects, nothing added or dropped
    assert _queue_names(campaign) == before  # suggesting changed nothing

    research.apply_queue_order(campaign, list(suggestion.order))
    assert _queue_names(campaign) == order
    assert research.get_queue_view(campaign).warnings_total == 0
    assert not research.suggest_queue_order(campaign).changed


def test_suggestion_preserves_a_valid_user_order_and_reports_unfixable_blockers(app, real):
    from app.services import research

    campaign = new_campaign("A")
    research.add_to_queue(campaign, oid("ModularWeapons"))
    research.add_to_queue(campaign, oid("AlienBiotech"))
    research.add_to_queue(campaign, oid("MagnetizedWeapons"))  # also needs Hybrid Materials + the autopsy: not queued
    suggestion = research.suggest_queue_order(campaign)

    assert not suggestion.changed  # nothing queued is out of order; user's order kept
    texts = " ".join(b.text for b in suggestion.unresolved)
    assert "Hybrid Materials" in texts and "ADVENT Officer Autopsy" in texts


def test_suggestion_survives_a_cycle_inside_the_queue(app, real, tmp_path):
    from app.services import research
    from app.services.content_import import import_pack

    objects = [{"template_key": k, "type": "research_project", "name": k} for k in ("LoopA", "LoopB")]
    rels = [
        {"type": "requires_research", "from": ["loops", "LoopA"], "to": ["loops", "LoopB"]},
        {"type": "requires_research", "from": ["loops", "LoopB"], "to": ["loops", "LoopA"]},
    ]
    import_pack(str(write_pack(tmp_path, "loops", objects=objects, relationships=rels, requires=["xcom2-base"])))
    campaign = new_campaign("A")
    attach_source(campaign, "loops")
    research.add_to_queue(campaign, oid("LoopA"))
    research.add_to_queue(campaign, oid("LoopB"))

    suggestion = research.suggest_queue_order(campaign)  # must terminate
    assert sorted(suggestion.order) == sorted([oid("LoopA"), oid("LoopB")])
    assert research.get_queue_view(campaign).warnings_total >= 1


# ------------------------------------------------ requires / unlocks


def test_requires_and_unlocks_cover_projects_facilities_items_and_resources(app, real):
    from app.services import research

    campaign = new_campaign("A")
    officer = research.get_research_detail(campaign, oid("AutopsyAdventOfficer"))
    req = {r.name: r for e in officer.requires for r in e.refs}
    assert "Alien Biotech" in req and req["Alien Biotech"].type_name == "Research Project"
    assert "ADVENT Officer Corpse" in req and req["ADVENT Officer Corpse"].amount == 1
    assert {u.name for u in officer.unlocks} >= {"Magnetic Weapons", "Skulljack", "Proving Ground"}
    kinds = {u.type_name for u in officer.unlocks}
    assert {"Research Project", "Facility", "Proving Ground Project"} <= kinds

    mag = research.get_research_detail(campaign, oid("MagnetizedWeapons"))
    assert {u.name for u in mag.unlocks} >= {"Gauss Weapons"}
    assert any(u.type_name == "Weapon" for u in mag.unlocks)
    armour = research.get_research_detail(campaign, oid("PlatedArmor"))
    assert any(u.type_name == "Armor" for u in armour.unlocks)


def test_or_requirements_are_displayed_as_alternatives_not_and(app, real):
    from app.services import research

    campaign = new_campaign("LWotC", "lwotc")
    detail = research.get_research_detail(campaign, oid("PlasmaRifle_LWOTC"))

    assert len(detail.requires) == 1 and detail.requires[0].logic == "any"
    assert {r.name for r in detail.requires[0].refs} == {"Advanced Coilguns", "Advanced Laser Weapons"}
    assert not detail.requires[0].satisfied and detail.ref.status == "blocked"
    advanced = research.get_research_detail(campaign, oid("AdvancedLasers"))
    assert any(u.name == "Plasma Rifle" and u.is_alternative for u in advanced.unlocks)


def test_and_requirements_are_separate_lines(app, real):
    from app.services import research

    detail = research.get_research_detail(new_campaign("A"), oid("MagnetizedWeapons"))
    assert [e.logic for e in detail.requires] == ["all", "all", "all"]
    assert {e.refs[0].name for e in detail.requires} == {"Modular Weapons", "Hybrid Materials", "ADVENT Officer Autopsy"}


# --------------------------------------------------------- browser


def test_browser_filters_and_search(app, real):
    from app.services import research
    from app.services.research import ResearchFilters

    campaign = new_campaign("LWotC", "lwotc")
    research.complete_research(campaign, oid("AlienBiotech"))
    f = lambda **k: [c.ref.name for c in research.browse_research(campaign, ResearchFilters(**k)).cards]

    assert set(f(category="autopsy")) == {"ADVENT Trooper Autopsy", "ADVENT Officer Autopsy", "Sectoid Autopsy", "Viper Autopsy", "Muton Autopsy"}
    assert f(status="completed") == ["Alien Biotech"]
    assert f(q="resistance communications") == ["Resistance Communications"]
    assert "Magnetic Weapons" in f(q="magnetic")
    assert len(f(q="magnetic")) > 1  # descriptions are searched as well as names
    assert f(q="zzzz-nothing") == []
    assert set(f(type_slug="proving_ground_project")) == {"Battle Scanner", "Battlefield Medicine", "Advanced Explosives", "Skulljack"}
    browse = research.browse_research(campaign)
    lw = next(sid for sid, name in browse.sources if "Long War" in name)
    assert set(f(source_id=lw)) >= {"Laser Weapons", "Coilguns"} and "Alien Biotech" not in f(source_id=lw)


def test_ruleset_filters_research_available_to_a_campaign(app, real):
    from app.services import research

    wotc = {c.ref.name for c in research.browse_research(new_campaign("W", "wotc-standard")).cards}
    lwotc = {c.ref.name for c in research.browse_research(new_campaign("L", "lwotc")).cards}
    assert "Laser Weapons" not in wotc and "Laser Weapons" in lwotc
    assert wotc < lwotc


# ----------------------------------------------------- unlock paths


def test_unlock_path_for_a_weapon_spans_every_level(app, real):
    from app.services import research

    campaign = new_campaign("A")
    path = research.get_unlock_path(campaign, oid("Cannon_MG"))  # weapon <- Gauss <- Magnetic <- 3 prerequisites <- Alien Biotech

    assert path.target.type_name == "Weapon" and path.graph.object_node(path.target.object_id).is_target
    steps = names(s.ref for s in path.mandatory_steps)
    assert steps == ["Alien Biotech", "ADVENT Officer Autopsy", "Hybrid Materials", "Modular Weapons", "Magnetic Weapons", "Gauss Weapons"]
    assert path.outstanding_steps == 6 and not path.target_met and not path.alternatives
    assert set(names(path.actionable)) == {"Alien Biotech", "Hybrid Materials", "Modular Weapons"}
    assert "ADVENT Officer Corpse" in {r.name for r in path.refs.values()}  # resource shown...
    assert "ADVENT Officer Corpse" not in names(path.missing)  # ...but never a blocker


def test_unlock_path_for_every_supported_target_type(app, real):
    from app.services import research

    campaign = new_campaign("A")
    cases = {
        "Cannon_MG": "Weapon", "PredatorArmor": "Armor", "PsiChamber": "Facility", "Mindshield": "Utility Item",
        "PlasmaRifle": "Research Project", "BattleScanner": "Proving Ground Project", "AssaultRifle_BM": "Weapon",
        "TracerRounds": "Ammunition",
    }  # fmt: skip
    for key, type_name in cases.items():
        path = research.get_unlock_path(campaign, oid(key))
        assert path.target.type_name == type_name, key
        assert len(path.graph.nodes) >= 1 and path.graph.analysis.mandatory_steps is not None


def test_unlock_path_to_a_facility_and_proving_ground_project(app, real):
    from app.services import research

    campaign = new_campaign("A")
    psi = research.get_unlock_path(campaign, oid("PsiChamber"))
    assert names(s.ref for s in psi.mandatory_steps) == ["Alien Biotech", "Sectoid Autopsy", "Psionics"]
    scanner = research.get_unlock_path(campaign, oid("BattleScanner"))
    assert names(s.ref for s in scanner.mandatory_steps) == ["Alien Biotech", "ADVENT Officer Autopsy", "ADVENT Trooper Autopsy", "Proving Ground"]


def test_campaign_status_decorates_the_graph(app, real):
    from app.services import research

    campaign = new_campaign("A")
    for key in ("AlienBiotech", "HybridMaterials"):
        research.complete_research(campaign, oid(key))
    research.start_research(campaign, oid("ModularWeapons"))
    research.add_to_queue(campaign, oid("AutopsyAdventOfficer"))
    path = research.get_unlock_path(campaign, oid("MagnetizedWeapons"))

    status = {r.name: r.status for r in path.refs.values()}
    assert status["Alien Biotech"] == "completed" and status["Hybrid Materials"] == "completed"
    assert status["Modular Weapons"] == "in_progress"
    assert status["ADVENT Officer Autopsy"] == "planned"
    assert status["Magnetic Weapons"] == "blocked"
    assert names(path.satisfied) == ["Alien Biotech", "Hybrid Materials"]
    assert set(names(path.actionable)) == {"Modular Weapons", "ADVENT Officer Autopsy"}


def test_completing_the_path_flips_the_target_to_available(app, real):
    from app.services import research

    campaign = new_campaign("A")
    for key in ("AlienBiotech", "HybridMaterials", "ModularWeapons", "AutopsyAdventOfficer"):
        research.complete_research(campaign, oid(key))
    path = research.get_unlock_path(campaign, oid("AssaultRifle_MG"))  # weapon unlocked by Magnetic Weapons
    assert path.outstanding_steps == 1 and names(path.actionable) == ["Magnetic Weapons"]
    research.complete_research(campaign, oid("MagnetizedWeapons"))
    path = research.get_unlock_path(campaign, oid("AssaultRifle_MG"))
    assert path.target_met and path.target.status == "available" and path.target.satisfied


def test_unlock_path_shows_alternative_branches_without_ranking_them(app, real):
    from app.services import research

    campaign = new_campaign("LWotC", "lwotc")
    path = research.get_unlock_path(campaign, oid("PlasmaRifle_LWOTC"))

    assert len(path.alternatives) == 1
    assert set(names(path.alternatives[0].options)) == {"Advanced Coilguns", "Advanced Laser Weapons"}
    assert names(s.ref for s in path.mandatory_steps) == ["Hybrid Materials", "Modular Weapons"]  # shared by both routes
    optional = set(names(s.ref for s in path.optional_steps))
    assert {"Laser Weapons", "Advanced Laser Weapons", "Coilguns", "Gauss Weapons", "Elerium"} <= optional
    gates = [n for n in path.graph.nodes if n.kind == "gate"]
    assert len(gates) == 1 and gates[0].logic == "OR"
    # both alternative branches are fully present in the graph
    assert {"Laser Weapons", "Advanced Coilguns", "Coilguns", "Elerium"} <= {r.name for r in path.refs.values()}


def test_completing_one_alternative_resolves_the_choice(app, real):
    from app.services import research

    campaign = new_campaign("LWotC", "lwotc")
    for key in ("HybridMaterials", "ModularWeapons", "LaserWeapons", "AdvancedLasers"):
        research.complete_research(campaign, oid(key))
    path = research.get_unlock_path(campaign, oid("PlasmaRifle_LWOTC"))
    assert path.target_met and not path.alternatives and path.target.status == "available"


def test_ruleset_specific_dependencies_differ_between_campaigns(app, real):
    from app.services import research

    wotc = research.get_unlock_path(new_campaign("W", "wotc-standard"), oid("PlasmaRifle"))
    lwotc = research.get_unlock_path(new_campaign("L", "lwotc"), oid("PlasmaRifle"))

    # Same logical object, different requirement expression per ruleset.
    assert lwotc.target.object_id != wotc.target.object_id  # the ruleset resolves a different object
    assert not wotc.alternatives and names(s.ref for s in wotc.mandatory_steps)[-3:] == ["Magnetic Weapons", "Gauss Weapons", "Elerium"]
    assert len(lwotc.alternatives) == 1
    assert "Elerium" not in names(s.ref for s in lwotc.mandatory_steps)  # LWotC replaced that requirement


def test_override_inherits_requirements_unless_it_declares_its_own(app, real, tmp_path):
    from app.services import research
    from app.services.content_import import import_pack

    objects = [
        {"template_key": "PlatedArmor_Mod", "type": "research_project", "name": "Plated Armor", "overrides": ["xcom2-base", "PlatedArmor"],
         "patch": {"attributes": {"research_category": "armor"}}},
    ]
    import_pack(str(write_pack(tmp_path, "inherit", objects=objects, requires=["xcom2-base"])))
    campaign = new_campaign("A", None)
    attach_source(campaign, "xcom2-base")
    attach_source(campaign, "inherit")
    detail = research.get_research_detail(campaign, oid("PlatedArmor"))  # base id resolves to the override
    assert [r.name for e in detail.requires for r in e.refs] == ["Hybrid Materials"]  # inherited, not lost


def test_mod_added_prerequisite_only_applies_to_campaigns_that_enable_it(app, real, tmp_path):
    from app.services import research
    from app.services.content_import import import_pack

    objects = [{"template_key": "ModGate", "type": "research_project", "name": "Mod Gate"}]
    rels = [{"type": "requires_research", "from": ["gated", "ModGate"], "to": ["xcom2-base", "PlatedArmor"]}]
    import_pack(str(write_pack(tmp_path, "gated", objects=objects, relationships=rels, requires=["xcom2-base"])))
    plain, modded = new_campaign("Plain", "wotc-standard"), new_campaign("Modded", "wotc-standard")
    attach_source(modded, "gated")

    assert [r.name for e in research.get_research_detail(plain, oid("PlatedArmor")).requires for r in e.refs] == ["Hybrid Materials"]
    assert {r.name for e in research.get_research_detail(modded, oid("PlatedArmor")).requires for r in e.refs} == {"Hybrid Materials", "Mod Gate"}


def test_custom_user_authored_object_is_a_valid_unlock_target(app, real):
    from app.models import CatalogObjectType
    from app.repositories import content_sources as sources_repo
    from app.services import catalog_objects as objects_service
    from app.services import catalog_relationships as rel_service
    from app.services import research

    campaign = new_campaign("A")
    user_source = sources_repo.get_content_source_by_slug("user-local")
    attach_source(campaign, "user-local")
    weapon_type = CatalogObjectType.query.filter_by(slug="weapon").first()
    custom = objects_service.create_object(source_id=user_source.id, type_id=weapon_type.id, name="Custom Pulse Rifle", is_user_authored=True)
    from app.extensions import db
    from app.models import CatalogObject, CatalogRelationshipType

    rel_service.create_relationship(
        relationship_type=CatalogRelationshipType.query.filter_by(slug="unlocks").first(),
        source_object=db.session.get(CatalogObject, oid("GaussWeapons")), target_object=custom, source_pack_id=user_source.id,
    )
    path = research.get_unlock_path(campaign, custom.id)
    assert path.target.name == "Custom Pulse Rifle"
    assert "Gauss Weapons" in names(s.ref for s in path.mandatory_steps)


def test_cycle_in_catalog_data_is_reported_not_followed(app, real, tmp_path):
    from app.services import research
    from app.services.content_import import import_pack

    objects = [{"template_key": k, "type": "research_project", "name": k} for k in ("CycA", "CycB", "CycC")]
    rels = [
        {"type": "requires_research", "from": ["cyc", "CycA"], "to": ["cyc", "CycB"]},
        {"type": "requires_research", "from": ["cyc", "CycB"], "to": ["cyc", "CycC"]},
        {"type": "requires_research", "from": ["cyc", "CycC"], "to": ["cyc", "CycA"]},
    ]
    import_pack(str(write_pack(tmp_path, "cyc", objects=objects, relationships=rels, requires=["xcom2-base"])))
    campaign = new_campaign("A")
    attach_source(campaign, "cyc")
    path = research.get_unlock_path(campaign, oid("CycC"))

    assert path.cycles and set(path.cycles[0]) == {"CycA", "CycB", "CycC"}
    assert not path.target_met and any(e.is_cycle for e in path.graph.edges)
    assert research.get_research_detail(campaign, oid("CycA")).ref.status == "blocked"


def test_unknown_or_foreign_targets_are_rejected(app, real):
    from app.services import research

    wotc = new_campaign("W", "wotc-standard")
    with pytest.raises(research.ResearchError):
        research.get_unlock_path(wotc, 987654)
    with pytest.raises(research.ResearchError):
        research.get_unlock_path(wotc, oid("LaserWeapons"))  # LWotC-only


def test_graph_payload_shape_and_status_cues(app, real):
    from app.services import research

    campaign = new_campaign("LWotC", "lwotc")
    research.complete_research(campaign, oid("HybridMaterials"))
    path = research.get_unlock_path(campaign, oid("PlasmaRifle_LWOTC"))
    payload = research.build_graph_payload(
        path, image_url=lambda i: f"/img/{i}", glyph_uri=lambda g, s: f"glyph:{s}", detail_url=lambda i: f"/d/{i}"
    )
    by_name = {n["data"].get("name"): n["data"] for n in payload.nodes if n["data"]["kind"] == "object"}

    assert "\u2713 DONE" in by_name["Hybrid Materials"]["label"]  # text cue, not colour alone
    assert "\u2715 LOCKED" in by_name["Advanced Coilguns"]["label"]
    assert by_name["Plasma Rifle"]["isTarget"] and "\u2605 TARGET" in by_name["Plasma Rifle"]["label"]
    assert by_name["Hybrid Materials"]["image"] == "glyph:completed"
    assert by_name["Hybrid Materials"]["url"].startswith("/d/")
    assert any(n["data"]["kind"] == "gate" and n["data"]["logic"] == "OR" for n in payload.nodes)
    ids = {n["data"]["id"] for n in payload.nodes}
    assert all(e["data"]["source"] in ids and e["data"]["target"] in ids for e in payload.edges)


def test_path_targets_exclude_resources_and_room_states(app, real):
    from app.services import research

    campaign = new_campaign("A")
    found = {r.type_slug for r in research.list_path_targets(campaign, limit=500)}
    assert not ({"resource", "avenger_room_state"} & found)
    assert {"weapon", "armor", "facility", "research_project", "proving_ground_project"} <= found
    assert [r.name for r in research.list_path_targets(campaign, q="plasma rifle")][:1] == ["Plasma Rifle"]
    types = dict(research.path_target_types(campaign))
    assert "weapon" in types and "resource" not in types


# ------------------------------------------------ avenger integration


def test_avenger_planner_and_research_share_one_notion_of_progress(app, real):
    from app.services import avenger, research

    campaign = new_campaign("A")
    pg = next(o.object_id for o in avenger.list_facility_options(campaign) if o.name == "Proving Ground")
    avenger.assign_facility(campaign, "R1C1", pg)
    assert avenger.get_cell_view(campaign, "R1C1").status == "planned_blocked"

    research.complete_research(campaign, oid("AutopsyAdventOfficer"))
    assert avenger.get_cell_view(campaign, "R1C1").status == "planned_available"
    # and a built facility satisfies Proving Ground projects in the research graph
    avenger.set_build_state(campaign, "R1C1", "built")
    path = research.get_unlock_path(campaign, oid("BattlefieldMedicine"))
    status = {r.name: r.status for r in path.refs.values()}
    assert status["Proving Ground"] == "completed"


# ------------------------------------------------- recommendation seam


def test_recommendation_seam_is_empty_and_separate_from_facts(app, real):
    from app.services import research

    campaign = new_campaign("A")
    assert research.recommendations_for(campaign, [oid("MagnetizedWeapons")]) == []

    class Provider:
        def recommendations_for(self, campaign, object_ids):
            return [research.Recommendation(object_ids[0], "Research Magnetic Weapons early", rationale="opinion")]

    original = research.get_recommendation_provider()
    research.set_recommendation_provider(Provider())
    try:
        advice = research.recommendations_for(campaign, [oid("MagnetizedWeapons")])
        assert advice[0].summary == "Research Magnetic Weapons early"
        # the factual requirement list is unaffected by any recommendation
        detail = research.get_research_detail(campaign, oid("MagnetizedWeapons"))
        assert {e.refs[0].name for e in detail.requires} == {"Modular Weapons", "Hybrid Materials", "ADVENT Officer Autopsy"}
    finally:
        research.set_recommendation_provider(original)
    assert research.recommendations_for(campaign, [oid("MagnetizedWeapons")]) == []
