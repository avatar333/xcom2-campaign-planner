"""Per-campaign recorded progress on catalog objects (ADR-0021, ADR-0010).

The single writer of `campaign_object_state` status transitions, shared by the
research planner and the Avenger planner's "mark prerequisite complete" action.
It only ever touches rows keyed `(campaign_id, catalog_object_id)`; the catalog
object itself is read, never changed (ADR-0002).

Completion is recorded with a timestamp *and* a per-campaign sequence number so
the order in which a campaign finished things survives identical timestamps.
"""
from __future__ import annotations

from sqlalchemy import func

from app.extensions import db
from app.models import Campaign, CampaignObjectState
from app.models.base import utc_now_iso
from app.services.dependency_index import SATISFIED_STATE_STATUSES, DependencyIndex


class ProgressError(ValueError):
    """Raised when progress is recorded for an object the campaign cannot see."""


def _winner(index: DependencyIndex, object_id: int) -> int:
    winner = index.winner_of(object_id)
    if winner is None:
        raise ProgressError("That object is not part of this campaign's catalog.")
    return winner


def _row(campaign: Campaign, object_id: int) -> CampaignObjectState | None:
    return CampaignObjectState.query.filter_by(campaign_id=campaign.id, catalog_object_id=object_id).first()


def _next_sequence(campaign: Campaign) -> int:
    current = (
        db.session.query(func.max(CampaignObjectState.completion_sequence))
        .filter(CampaignObjectState.campaign_id == campaign.id)
        .scalar()
    )
    return (current or 0) + 1


def _all_rows_for(campaign: Campaign, index: DependencyIndex, winner: int) -> list[CampaignObjectState]:
    return [
        r
        for r in CampaignObjectState.query.filter_by(campaign_id=campaign.id).all()
        if index.normalize(r.catalog_object_id) == winner
    ]


def mark_completed(
    campaign: Campaign, object_id: int, *, index: DependencyIndex | None = None, commit: bool = True
) -> CampaignObjectState:
    """Record the object complete for this campaign (idempotent: re-completing
    keeps the original timestamp and sequence)."""
    index = index or DependencyIndex(campaign)
    winner = _winner(index, object_id)
    row = _row(campaign, winner)
    if row is None:
        row = CampaignObjectState(campaign_id=campaign.id, catalog_object_id=winner, status="unknown")
        db.session.add(row)
    if row.status not in SATISFIED_STATE_STATUSES or row.completion_sequence is None:
        row.status = "completed" if row.status not in SATISFIED_STATE_STATUSES else row.status
        row.completed_at = row.completed_at or utc_now_iso()
        row.completion_sequence = row.completion_sequence or _next_sequence(campaign)
    if commit:
        db.session.commit()
    return row


def mark_in_progress(
    campaign: Campaign, object_id: int, *, index: DependencyIndex | None = None, commit: bool = True
) -> CampaignObjectState:
    index = index or DependencyIndex(campaign)
    winner = _winner(index, object_id)
    row = _row(campaign, winner)
    if row is None:
        row = CampaignObjectState(campaign_id=campaign.id, catalog_object_id=winner, status="in_progress")
        db.session.add(row)
    else:
        row.status = "in_progress"
        row.completed_at = None
        row.completion_sequence = None
    if commit:
        db.session.commit()
    return row


def reset(
    campaign: Campaign, object_id: int, *, index: DependencyIndex | None = None, commit: bool = True
) -> None:
    """Forget recorded progress (completed / in progress) for this campaign."""
    index = index or DependencyIndex(campaign)
    winner = _winner(index, object_id)
    for row in _all_rows_for(campaign, index, winner):
        row.status = "unknown"
        row.completed_at = None
        row.completion_sequence = None
    if commit:
        db.session.commit()


def get_state(campaign: Campaign, object_id: int) -> CampaignObjectState | None:
    index = DependencyIndex(campaign)
    return index.state_row(object_id)
