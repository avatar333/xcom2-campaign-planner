"""App factory smoke tests (IMPLEMENTATION_PLAN.md Phase 2)."""
from __future__ import annotations

from app import create_app


def test_create_app_default_config():
    flask_app = create_app("testing")
    assert flask_app is not None
    assert flask_app.config["TESTING"] is True


def test_create_app_registers_blueprints(app):
    blueprint_names = set(app.blueprints.keys())
    assert {"dashboard", "campaigns", "catalog", "planning"} <= blueprint_names


def test_app_can_service_requests(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Dashboard" in response.data


def test_sqlite_foreign_keys_enabled(app, db):
    with app.app_context():
        result = db.session.execute(db.text("PRAGMA foreign_keys")).scalar()
        assert result == 1
