from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.db.models import (
    Alert,
    FairValue,
    LLMClassification,
    Market,
    MarketSnapshot,
    PPISignal,
    PaperTrade,
    RawAPIResponse,
    ResolutionRisk,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def upsert_market(session: Session, payload: dict[str, Any]) -> Market:
    platform = payload.get("platform", "polymarket")
    platform_market_id = str(payload["platform_market_id"])
    market = session.scalar(
        select(Market).where(
            Market.platform == platform,
            Market.platform_market_id == platform_market_id,
        )
    )
    if market is None:
        market = Market(platform=platform, platform_market_id=platform_market_id, question=payload.get("question", ""))
        session.add(market)
        session.flush()
    for key, value in payload.items():
        if hasattr(market, key):
            setattr(market, key, value)
    market.last_seen_at = utcnow()
    return market


def create_snapshot(session: Session, market_id: int, payload: dict[str, Any]) -> MarketSnapshot:
    snapshot = MarketSnapshot(market_id=market_id, **payload)
    session.add(snapshot)
    session.flush()
    return snapshot


def upsert_fair_value(session: Session, market_id: int, payload: dict[str, Any]) -> FairValue:
    row = session.scalar(select(FairValue).where(FairValue.market_id == market_id))
    if row is None:
        row = FairValue(market_id=market_id)
        session.add(row)
    for key, value in payload.items():
        if hasattr(row, key):
            setattr(row, key, value)
    row.updated_at = utcnow()
    session.flush()
    return row


def latest_snapshot(session: Session, market_id: int) -> Optional[MarketSnapshot]:
    return session.scalar(
        select(MarketSnapshot)
        .where(MarketSnapshot.market_id == market_id)
        .order_by(MarketSnapshot.timestamp.desc())
        .limit(1)
    )


def latest_fair_value(session: Session, market_id: int) -> Optional[FairValue]:
    return session.scalar(select(FairValue).where(FairValue.market_id == market_id))


def latest_classification(session: Session, market_id: int) -> Optional[LLMClassification]:
    return session.scalar(
        select(LLMClassification)
        .where(LLMClassification.market_id == market_id)
        .order_by(LLMClassification.timestamp.desc())
        .limit(1)
    )


def latest_resolution_risk(session: Session, market_id: int) -> Optional[ResolutionRisk]:
    return session.scalar(
        select(ResolutionRisk)
        .where(ResolutionRisk.market_id == market_id)
        .order_by(ResolutionRisk.timestamp.desc())
        .limit(1)
    )


def create_llm_classification(session: Session, market_id: int, model: str, data: dict[str, Any]) -> LLMClassification:
    row = LLMClassification(
        market_id=market_id,
        model=model,
        market_category=data.get("market_category"),
        emotional_side=data.get("emotional_side"),
        ideological_coding=data.get("ideological_coding"),
        identity_intensity=data.get("identity_intensity"),
        institutional_friction=data.get("institutional_friction"),
        deadline_decay_relevance=data.get("deadline_decay_relevance"),
        classification_confidence=data.get("classification_confidence"),
        summary=data.get("summary"),
        warnings_json=dumps(data.get("warnings", [])),
        raw_json=dumps(data),
    )
    session.add(row)
    session.flush()
    return row


def create_resolution_risk(session: Session, market_id: int, model: str, data: dict[str, Any]) -> ResolutionRisk:
    row = ResolutionRisk(
        market_id=market_id,
        model=model,
        resolution_risk=data.get("resolution_risk"),
        ambiguous_terms_json=dumps(data.get("ambiguous_terms", [])),
        source_dependency=data.get("source_dependency"),
        implementation_vs_announcement_risk=data.get("implementation_vs_announcement_risk"),
        date_boundary_risk=data.get("date_boundary_risk"),
        summary=data.get("summary"),
        warnings_json=dumps(data.get("warnings", [])),
        raw_json=dumps(data),
    )
    session.add(row)
    session.flush()
    return row


def create_signal(session: Session, market_id: int, payload: dict[str, Any]) -> PPISignal:
    signal = PPISignal(market_id=market_id, **payload)
    session.add(signal)
    session.flush()
    return signal


def create_paper_trade(session: Session, payload: dict[str, Any]) -> PaperTrade:
    trade = PaperTrade(**payload)
    session.add(trade)
    session.flush()
    return trade


def create_alert(session: Session, payload: dict[str, Any]) -> Alert:
    alert = Alert(**payload)
    session.add(alert)
    session.flush()
    return alert


def log_raw_api_response(
    session: Session,
    source: str,
    endpoint: str,
    request_params: dict[str, Any] | None,
    response: Any | None,
    status_code: int | None = None,
    error_message: str | None = None,
) -> RawAPIResponse:
    row = RawAPIResponse(
        source=source,
        endpoint=endpoint,
        request_params_json=dumps(request_params or {}),
        response_json=dumps(response) if response is not None else None,
        status_code=status_code,
        error_message=error_message,
    )
    session.add(row)
    session.flush()
    return row


def active_markets_query() -> Select[tuple[Market]]:
    return select(Market).where(Market.active.is_(True), Market.closed.is_(False))


def list_active_markets(session: Session, limit: int | None = None) -> list[Market]:
    stmt = active_markets_query().order_by(Market.last_seen_at.desc())
    if limit:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))
