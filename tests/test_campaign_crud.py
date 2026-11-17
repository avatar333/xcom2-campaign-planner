"""Campaign CRUD + validation tests (FR-CMP-1..6)."""
from __future__ import annotations

import pytest

from app.models import Campaign
from app.services import campaign_lifecycle
from app.services.campaign_lifecycle import CampaignValidationError


def test_create_campaign_minimal(app, seeded_db):
    with app.app_context():
        campaign = campaign_lifecycle.create_campaign(
            name="Operation Avalanche", game_profile_slug=None
        )
        assert campaign.id is not None
        assert campaign.name == "Operation Avalanche"
        assert campaign.difficulty == "commander"
        assert campaign.status == "active"
        assert campaign.created_at
        assert campaign.updated_at


def test_create_campaign_with_game_profile_prefills_sources(app, seeded_db):
    with app.app_context():
        campaign = campaign_lifecycle.create_campaign(
            name="Operation Firestorm", game_profile_slug="wotc-standard"
        )
        slugs = sorted(
            cs.content_source.slug for cs in campaign.content_sources
        )
        assert slugs == ["wotc", "xcom2-base"]


def test_create_campaign_with_lwotc_profile(app, seeded_db):
    with app.app_context():
        campaign = campaign_lifecycle.create_campaign(
            name="Operation Deep Strike", game_profile_slug="lwotc"
        )
        slugs = sorted(cs.content_source.slug for cs in campaign.content_sources)
        assert slugs == ["lwotc", "wotc", "xcom2-base"]


def test_create_campaign_requires_name(app, seeded_db):
    with app.app_context(), pytest.raises(CampaignValidationError):
        campaign_lifecycle.create_campaign(name="   ", game_profile_slug=None)


def test_create_campaign_rejects_overlong_name(app, seeded_db):
    with app.app_context(), pytest.raises(CampaignValidationError):
        campaign_lifecycle.create_campaign(name="x" * 201, game_profile_slug=None)


def test_update_campaign(app, seeded_db):
    with app.app_context():
        campaign = campaign_lifecycle.create_campaign(
            name="Original Name", game_profile_slug=None
        )

        updated = campaign_lifecycle.update_campaign(
            campaign,
            name="Renamed Operation",
            difficulty="legend",
            is_ironman=True,
            notes="High risk run.",
        )

        assert updated.name == "Renamed Operation"
        assert updated.difficulty == "legend"
        assert updated.is_ironman is True
        assert updated.notes == "High risk run."


def test_archive_and_reactivate_campaign(app, seeded_db):
    with app.app_context():
        campaign = campaign_lifecycle.create_campaign(name="Shelved Run", game_profile_slug=None)
        campaign_lifecycle.archive_campaign(campaign)
        assert campaign.status == "archived"

        campaign_lifecycle.reactivate_campaign(campaign)
        assert campaign.status == "active"


def test_delete_campaign_removes_it(app, seeded_db, db):
    with app.app_context():
        campaign = campaign_lifecycle.create_campaign(name="Doomed Run", game_profile_slug=None)
        campaign_id = campaign.id

        campaign_lifecycle.delete_campaign(campaign)

        assert db.session.get(Campaign, campaign_id) is None


def test_list_campaigns_orders_by_recently_updated(app, seeded_db):
    with app.app_context():
        campaign_lifecycle.create_campaign(name="First", game_profile_slug=None)
        second = campaign_lifecycle.create_campaign(name="Second", game_profile_slug=None)
        campaign_lifecycle.update_campaign(
            second, name="Second (updated)", difficulty="commander", is_ironman=False
        )

        from app.repositories.campaigns import list_campaigns

        campaigns = list_campaigns()
        assert campaigns[0].name == "Second (updated)"
