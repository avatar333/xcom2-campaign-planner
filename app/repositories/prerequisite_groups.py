"""Compound (nested AND/OR) prerequisite-group repository (`ADR-0018`)."""
from __future__ import annotations

from app.extensions import db
from app.models import CatalogPrerequisiteGroup, CatalogPrerequisiteGroupMember


def create_group(**fields) -> CatalogPrerequisiteGroup:
    group = CatalogPrerequisiteGroup(**fields)
    db.session.add(group)
    db.session.flush()
    return group


def add_member(**fields) -> CatalogPrerequisiteGroupMember:
    member = CatalogPrerequisiteGroupMember(**fields)
    db.session.add(member)
    db.session.flush()
    return member


def list_root_groups_for_object(target_object_id: int) -> list[CatalogPrerequisiteGroup]:
    return CatalogPrerequisiteGroup.query.filter_by(
        target_object_id=target_object_id, parent_group_id=None
    ).all()


def get_group(group_id: int) -> CatalogPrerequisiteGroup | None:
    return db.session.get(CatalogPrerequisiteGroup, group_id)


def list_members(group_id: int) -> list[CatalogPrerequisiteGroupMember]:
    return (
        CatalogPrerequisiteGroupMember.query.filter_by(group_id=group_id)
        .order_by(CatalogPrerequisiteGroupMember.sort_order.asc())
        .all()
    )
