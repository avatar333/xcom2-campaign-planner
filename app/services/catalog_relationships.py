"""Catalog relationship lifecycle service (FR-CAT-3, ADR-0004).

Validation rules implemented here:
- No self-relations (an object cannot relate to itself).
- No exact duplicates (same type + source + target + group_key).
- Relationships are *not* assumed acyclic in general (`supersedes`,
  `upgrades_to` style chains can legitimately loop back in modded content,
  per the brief) -- we do not reject cycles, but we do flag `prerequisite`
  edges that would create a direct A<->B cycle, since that specific
  relationship kind's semantics (used by the future requirement resolver,
  ARCHITECTURE.md §6) genuinely cannot be satisfied if cyclic.
"""
from __future__ import annotations

from app.models import CatalogObject, CatalogRelationship, CatalogRelationshipType
from app.repositories import catalog_relationships as repo

# Relationship kinds whose semantics require a strictly acyclic graph --
# "X is a prerequisite of Y" cannot also have "Y is a prerequisite of X".
_ACYCLIC_REQUIRED_SLUGS = {"prerequisite", "requires_facility", "requires_research"}


class RelationshipValidationError(ValueError):
    pass


def create_relationship(
    *,
    relationship_type: CatalogRelationshipType,
    source_object: CatalogObject,
    target_object: CatalogObject,
    group_key: str | None = None,
    group_logic: str | None = None,
    amount: float | None = None,
    unit: str | None = None,
    source_pack_id: int,
    is_user_authored: bool = True,
) -> CatalogRelationship:
    if source_object.id == target_object.id:
        raise RelationshipValidationError("An object cannot have a relationship with itself.")

    group_key = (group_key or "").strip() or None
    if group_key and group_logic not in ("AND", "OR"):
        group_logic = "OR"

    duplicate = repo.find_duplicate(
        relationship_type_id=relationship_type.id,
        source_object_id=source_object.id,
        target_object_id=target_object.id,
        group_key=group_key,
    )
    if duplicate is not None:
        raise RelationshipValidationError(
            f"A '{relationship_type.name}' relationship between these two objects already exists."
        )

    if relationship_type.slug in _ACYCLIC_REQUIRED_SLUGS:
        inverse_duplicate = repo.find_duplicate(
            relationship_type_id=relationship_type.id,
            source_object_id=target_object.id,
            target_object_id=source_object.id,
            group_key=group_key,
        )
        if inverse_duplicate is not None:
            raise RelationshipValidationError(
                f"Creating this would make a direct cycle: '{target_object.name}' already has a "
                f"'{relationship_type.name}' relationship pointing back at '{source_object.name}'."
            )

    if relationship_type.is_quantified and amount is None:
        raise RelationshipValidationError(
            f"'{relationship_type.name}' requires an amount."
        )

    return repo.create_relationship(
        relationship_type_id=relationship_type.id,
        source_object_id=source_object.id,
        target_object_id=target_object.id,
        group_key=group_key,
        group_logic=group_logic,
        amount=amount,
        unit=(unit or "").strip() or None,
        source_pack_id=source_pack_id,
        is_user_authored=is_user_authored,
    )


def delete_relationship(relationship: CatalogRelationship) -> None:
    repo.delete_relationship(relationship)


def relationships_for_object(object_id: int) -> dict[str, list]:
    outbound, inbound = repo.list_for_object(object_id)
    return {"outbound": outbound, "inbound": inbound}
