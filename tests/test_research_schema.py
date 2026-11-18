"""Schema-level guarantees for Phase 6 tables (ADR-0021, ADR-0010)."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from tests.avenger_helpers import import_real_packs, new_campaign


def test_queue_table_is_campaign_scoped_with_cascade(app, db):
    with app.app_context():
        inspector = inspect(db.engine)
        fks = {fk["constrained_columns"][0]: fk for fk in inspector.get_foreign_keys("campaign_research_queue")}
        assert fks["campaign_id"]["referred_table"] == "campaigns"
        assert fks["campaign_id"]["options"].get("ondelete") == "CASCADE"
        assert fks["catalog_object_id"]["options"].get("ondelete") == "RESTRICT"
        uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("campaign_research_queue")}
        assert ("campaign_id", "catalog_object_id") in uniques


def test_completion_columns_exist_on_object_state(app, db):
    with app.app_context():
        columns = {c["name"] for c in inspect(db.engine).get_columns("campaign_object_state")}
        assert {"completed_at", "completion_sequence"} <= columns


def test_database_rejects_duplicate_queue_entries_and_negative_positions(app, seeded_db):
    from app.extensions import db
    from app.models import CampaignResearchQueueItem, CatalogObject

    with app.app_context():
        import_real_packs()
        campaign = new_campaign("A")
        biotech = CatalogObject.query.filter_by(template_key="AlienBiotech").first()
        db.session.add(CampaignResearchQueueItem(campaign_id=campaign.id, catalog_object_id=biotech.id, position=0))
        db.session.commit()
        db.session.add(CampaignResearchQueueItem(campaign_id=campaign.id, catalog_object_id=biotech.id, position=1))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        other = CatalogObject.query.filter_by(template_key="HybridMaterials").first()
        db.session.add(CampaignResearchQueueItem(campaign_id=campaign.id, catalog_object_id=other.id, position=-1))
        with pytest.raises(IntegrityError):
            db.session.commit()


def test_referenced_catalog_object_cannot_be_hard_deleted(app, seeded_db):
    from app.extensions import db
    from app.models import CatalogObject
    from app.services import research

    with app.app_context():
        import_real_packs()
        campaign = new_campaign("A")
        biotech = CatalogObject.query.filter_by(template_key="AlienBiotech").first()
        research.add_to_queue(campaign, biotech.id)
        db.session.delete(biotech)
        with pytest.raises(IntegrityError):
            db.session.commit()
