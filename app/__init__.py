"""Flask application factory (ARCHITECTURE.md §1-2, ADR-0011, ADR-0014)."""
from __future__ import annotations

from pathlib import Path

from flask import Flask
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.config import get_config
from app.extensions import csrf, db, migrate


def _enable_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """SQLite does not enable FK enforcement or WAL by default (ADR-0014)."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


_PRAGMA_LISTENER_REGISTERED = False


def _ensure_pragma_listener_registered() -> None:
    global _PRAGMA_LISTENER_REGISTERED
    if not _PRAGMA_LISTENER_REGISTERED:
        event.listen(Engine, "connect", _enable_sqlite_pragmas)
        _PRAGMA_LISTENER_REGISTERED = True


def create_app(config_name: str | None = None, instance_path: str | None = None) -> Flask:
    app = Flask(
        __name__,
        instance_relative_config=True,
        instance_path=instance_path,
    )
    app.config.from_object(get_config(config_name))

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["ASSETS_PATH"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)

    _ensure_pragma_listener_registered()
    from app.cli import register_cli

    register_cli(app)

    from app.routes.avenger import avenger_bp
    from app.routes.campaigns import campaigns_bp
    from app.routes.catalog import catalog_bp
    from app.routes.content_sources import content_sources_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.planning import planning_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(campaigns_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(content_sources_bp)
    app.register_blueprint(planning_bp)
    app.register_blueprint(avenger_bp)

    @app.context_processor
    def inject_nav_context():
        from app.services.campaign_context import get_active_campaign

        return {"active_campaign": get_active_campaign()}

    return app
