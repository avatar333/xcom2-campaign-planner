"""Custom CLI commands (ARCHITECTURE.md §2, PRODUCT_SPEC FR-PLT-3).

`flask db upgrade`/`flask db migrate` come from Flask-Migrate itself
(ADR-0014); this module only adds `flask seed-dev`.
"""
from __future__ import annotations

import click
from flask import Flask

from app.services.seed import seed_dev


def register_cli(app: Flask) -> None:
    @app.cli.command("seed-dev")
    def seed_dev_command() -> None:
        """Seed foundational content sources and builtin catalog types/relationship types.

        Idempotent -- safe to run against an already-seeded database. Does
        NOT populate game objects (see DATABASE_SCHEMA.md §9).
        """
        counts = seed_dev()
        click.echo(
            "Seeded: "
            f"{counts['content_sources']} content source(s), "
            f"{counts['object_types']} object type(s), "
            f"{counts['relationship_types']} relationship type(s)."
        )
