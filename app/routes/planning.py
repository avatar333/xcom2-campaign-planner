"""Planning -- Phase 7 implements this fully (IMPLEMENTATION_PLAN.md).

Placeholder screen only, same rationale as app/routes/catalog.py.
"""
from __future__ import annotations

from flask import Blueprint, render_template

planning_bp = Blueprint("planning", __name__, url_prefix="/planning")


@planning_bp.route("/")
def index():
    return render_template("planning/placeholder.html")
