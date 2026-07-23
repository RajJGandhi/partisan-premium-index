from __future__ import annotations

import json
from typing import Any

from app.db import crud
from app.db.database import get_session, init_db
from app.db.models import LLMClassification, ResolutionRisk
from app.llm.classifiers import LocalLLMClassifiers
from app.scoring.fair_value import compute_fair_value
from app.scoring.ppi_score import PPIInputs, compute_ppi


def _row_float(value):
    return None if value is None else float(value)


def run(limit: int | None = None, use_llm: bool = True) -> int:
    init_db()
    classifiers = LocalLLMClassifiers() if use_llm else None
    scored = 0
    with get_session() as session:
        markets = crud.list_active_markets(session, limit=limit)
        for market in markets:
            snapshot = crud.latest_snapshot(session, market.id)
            fv = crud.latest_fair_value(session, market.id)
            if fv is None:
                continue
            computed = compute_fair_value(
                polling_prob=fv.polling_prob,
                forecast_prob=fv.forecast_prob,
                other_markets_prob=fv.other_markets_prob,
                expert_prob=fv.expert_prob,
                news_campaign_prob=fv.news_campaign_prob,
                manual_fair_yes=fv.manual_fair_yes,
                confidence=fv.confidence,
            )
            fv.computed_fair_yes = computed.fair_yes
            fv.confidence = computed.adjusted_confidence
            classification = crud.latest_classification(session, market.id)
            resolution = crud.latest_resolution_risk(session, market.id)
            if use_llm and classifiers and classification is None:
                payload = {
                    "question": market.question,
                    "description": market.description or "",
                    "rules": market.rules or "",
                    "outcomes": json.loads(market.outcomes_json or "[]") if market.outcomes_json else [],
                    "tags": json.loads(market.tags_json or "[]") if market.tags_json else [],
                    "end_date": str(market.end_date or ""),
                }
                result = classifiers.classify_market(payload)
                classification = crud.create_llm_classification(
                    session, market.id, classifiers.client.model, result.model_dump()
                )
            if use_llm and classifiers and resolution is None:
                payload = {
                    "question": market.question,
                    "rules": market.rules or "",
                    "resolution_source": market.resolution_source or "",
                    "end_date": str(market.end_date or ""),
                }
                result = classifiers.parse_resolution_risk(payload)
                resolution = crud.create_resolution_risk(
                    session, market.id, classifiers.client.model, result.model_dump()
                )
            inputs = PPIInputs(
                polymarket_yes=snapshot.yes_best_ask if snapshot else None,
                fair_yes=computed.fair_yes,
                emotional_side=classification.emotional_side if classification else "unclear",
                identity_intensity=classification.identity_intensity if classification else 0,
                institutional_friction=classification.institutional_friction if classification else 0,
                deadline_decay_relevance=classification.deadline_decay_relevance if classification else 0,
                end_date=market.end_date,
                spread=snapshot.spread if snapshot else None,
                depth_3c=snapshot.depth_3c if snapshot else None,
                resolution_risk=resolution.resolution_risk if resolution else 3,
                fair_value_confidence=computed.adjusted_confidence,
            )
            ppi = compute_ppi(inputs)
            crud.create_signal(
                session,
                market.id,
                {
                    "polymarket_yes": inputs.polymarket_yes,
                    "fair_yes": inputs.fair_yes,
                    "premium": ppi.premium,
                    "ppi_score": ppi.ppi_score,
                    "action": ppi.action,
                    "paper_side": ppi.paper_side,
                    "score_breakdown_json": json.dumps(ppi.score_breakdown, default=str),
                    "explanation": "Deterministic PPI score generated from fair value, executable price, liquidity, classification, and resolution risk.",
                    "warnings_json": json.dumps(ppi.warnings),
                    "status": "active",
                },
            )
            scored += 1
    return scored


if __name__ == "__main__":
    count = run()
    print(f"Generated {count} PPI signals.")
