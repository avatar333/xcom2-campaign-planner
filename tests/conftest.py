"""Shared pytest fixtures.

Each test gets a fresh temp-file SQLite database with the full Alembic
migration history applied (never the dev DB, never :memory: for anything
that needs to survive a second connection -- see ARCHITECTURE.md §10 /
PRODUCT_SPEC NFR-2,3). Fully isolated per test function.
"""
from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

from app import create_app
from app.extensions import db as _db


@pytest.fixture()
def app(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    assets_path = tmp_path / "assets"
    instance_path = tmp_path / "instance"
    instance_path.mkdir()

    os.environ["DATABASE_PATH"] = str(db_path)
    os.environ["ASSETS_PATH"] = str(assets_path)

    flask_app = create_app("testing", instance_path=str(instance_path))
    flask_app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    with flask_app.app_context():
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        alembic_cfg = AlembicConfig(os.path.join(repo_root, "migrations", "alembic.ini"))
        alembic_cfg.set_main_option("script_location", os.path.join(repo_root, "migrations"))
        command.upgrade(alembic_cfg, "head")

    yield flask_app

    with flask_app.app_context():
        _db.session.remove()
        _db.engine.dispose()


@pytest.fixture()
def db(app):
    with app.app_context():
        yield _db


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def seeded_db(db):
    from app.services.seed import seed_dev

    seed_dev()
    return db
