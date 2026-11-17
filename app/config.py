"""Environment-separated configuration (ARCHITECTURE.md §4)."""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def _instance_path() -> Path:
    return Path(__file__).resolve().parent.parent / "instance"


def _resolve_instance_relative(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = _instance_path() / path
    return path


class Config:
    """Base configuration shared by every environment."""

    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "8"))
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "8")) * 1024 * 1024
    ASSETS_PATH = str(_resolve_instance_relative(os.environ.get("ASSETS_PATH", "assets")))
    WTF_CSRF_ENABLED = True

    @staticmethod
    def _sqlite_uri(db_path: str) -> str:
        path = _resolve_instance_relative(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = Config._sqlite_uri(
        os.environ.get("DATABASE_PATH", "app.sqlite3")
    )


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    # Overridden per-test to a tmp_path file; default kept in-memory for ad hoc use.
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = Config._sqlite_uri(
        os.environ.get("DATABASE_PATH", "app.sqlite3")
    )


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None) -> type[Config]:
    name = name or os.environ.get("FLASK_ENV", "development")
    return CONFIG_BY_NAME.get(name, DevelopmentConfig)
