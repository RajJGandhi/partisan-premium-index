from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    FairValue,
    FairValueProposal,
    FairValueRevision,
    Market,
    MarketResolution,
    MarketSnapshot,
    Prediction,
)
from app.ppi.methodology import brier_score


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def latest_daily_snapshot(session: Session, market_id: int) -> MarketSnapshot | None:
    return session.scalar(
        select(MarketSnapshot)
        .where(MarketSnapshot.market_id == market_id, MarketSnapshot.snapshot_kind == "daily")
        .order_by(MarketSnapshot.snapshot_date.desc(), MarketSnapshot.timestamp.desc())
        .limit(1)
    )


def publish_proposal(
    session: Session,
    proposal: FairValueProposal,
    fair_value: float | None = None,
    thesis: str | None = None,
    justification: str | None = None,
    reviewer: str = "admin",
) -> FairValueRevision:
    if proposal.status != "PENDING":
        raise ValueError("Only pending proposals can be published")
    value = float(fair_value if fair_value is not None else proposal.proposed_fair_value)
    if not 0 <= value <= 1:
        raise ValueError("Fair value must be between 0 and 1")
    market = session.get(Market, proposal.market_id)
    if not market:
        raise ValueError("Market not found")
    fv = session.scalar(select(FairValue).where(FairValue.market_id == market.id))
    if not fv:
        fv = FairValue(market_id=market.id)
        session.add(fv)
        session.flush()
    previous = fv.published_fair_yes
    number = (
        int(
            session.scalar(select(func.count(FairValueRevision.id)).where(FairValueRevision.market_id == market.id))
            or 0
        )
        + 1
    )
    revision = FairValueRevision(
        market_id=market.id,
        proposal_id=proposal.id,
        revision_number=number,
        fair_value=value,
        previous_fair_value=previous,
        components_json=proposal.proposed_components_json,
        weights_json=proposal.proposed_weights_json,
        effective_weights_json=proposal.effective_weights_json,
        evidence_ids_json=proposal.evidence_ids_json,
        thesis=thesis or market.current_thesis or proposal.rationale,
        justification=justification or proposal.rationale,
        published_by=reviewer,
    )
    session.add(revision)
    session.flush()
    fv.published_fair_yes = value
    fv.computed_fair_yes = value
    fv.last_published_at = revision.published_at
    fv.last_updated = revision.published_at
    market.current_thesis = revision.thesis
    proposal.status = "APPROVED"
    proposal.reviewed_at = utcnow()
    proposal.reviewed_by = reviewer
    proposal.review_notes = justification or proposal.rationale

    existing_prediction = session.scalar(select(Prediction).where(Prediction.market_id == market.id))
    if not existing_prediction:
        snap = latest_daily_snapshot(session, market.id)
        prediction = Prediction(
            market_id=market.id,
            initial_revision_id=revision.id,
            initial_publication_at=revision.published_at,
            initial_fair_value=value,
            initial_market_probability=snap.comparison_price if snap else None,
            initial_thesis=revision.thesis,
            supporting_evidence_ids_json=revision.evidence_ids_json,
        )
        session.add(prediction)
    session.flush()
    return revision


def reject_proposal(session: Session, proposal: FairValueProposal, reviewer: str, notes: str) -> None:
    if proposal.status != "PENDING":
        raise ValueError("Only pending proposals can be rejected")
    proposal.status = "REJECTED"
    proposal.reviewed_at = utcnow()
    proposal.reviewed_by = reviewer
    proposal.review_notes = notes


def resolve_market(
    session: Session,
    market: Market,
    outcome: float,
    source_url: str | None,
    notes: str | None,
    recorded_by: str = "admin",
) -> MarketResolution:
    if outcome not in (0, 1, 0.0, 1.0):
        raise ValueError("Resolution outcome must be 0 or 1")
    resolution = session.scalar(select(MarketResolution).where(MarketResolution.market_id == market.id))
    if resolution:
        raise ValueError("Market is already resolved; corrections must be recorded separately")
    resolution = MarketResolution(
        market_id=market.id,
        resolved_outcome=float(outcome),
        resolved_label="YES" if outcome == 1 else "NO",
        source_url=source_url,
        notes=notes,
        recorded_by=recorded_by,
    )
    session.add(resolution)
    market.status = "RESOLVED"
    market.closed = True
    predictions = list(session.scalars(select(Prediction).where(Prediction.market_id == market.id)))
    for pred in predictions:
        pred.status = "RESOLVED"
        pred.final_outcome = float(outcome)
        pred.resolved_at = resolution.resolved_at
        pred.ppi_brier_score = brier_score(pred.initial_fair_value, float(outcome))
        if pred.initial_market_probability is not None:
            pred.market_brier_score = brier_score(pred.initial_market_probability, float(outcome))
            pred.performance_difference = pred.market_brier_score - pred.ppi_brier_score
    session.flush()
    return resolution


def proposal_payload(proposal: FairValueProposal) -> dict:
    return {
        "id": proposal.id,
        "market_id": proposal.market_id,
        "proposed_fair_value": proposal.proposed_fair_value,
        "current_published_fair_value": proposal.current_published_fair_value,
        "components": json.loads(proposal.proposed_components_json or "{}"),
        "weights": json.loads(proposal.proposed_weights_json or "{}"),
        "effective_weights": json.loads(proposal.effective_weights_json or "{}"),
        "evidence_ids": json.loads(proposal.evidence_ids_json or "[]"),
        "rationale": proposal.rationale,
        "confidence": proposal.confidence,
        "status": proposal.status,
    }
