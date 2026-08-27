"""Stage 1 -- automatic Polymarket market discovery + race binding (spec section 42).

Discovered political contracts are classified deterministically (LLM fallback for ambiguous). A
``SUPPORTED_STATEWIDE_RACE`` above the confidence threshold is **ACCEPTED**: a ``markets`` row and a
``races`` row are upserted and linked, and ``races.market_yes_party`` is set from the question
wording. Everything else (``SUPPORTED_SENATE_CONTROL`` / ``SUPPORTED_HOUSE_CONTROL`` /
``AMBIGUOUS`` / ``UNSUPPORTED``) is **QUARANTINED** -- recorded, shown with market price + blind
LLM only, never given a fabricated Quant forecast. Every decision is an append-only
``market_classifications`` row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Market
from app.db.models_quant import MarketClassification, Race
from app.providers.markets import (
    SUPPORTED_STATEWIDE_RACE,
    DiscoveredMarket,
    PolymarketDiscoveryProvider,
)


@dataclass
class DiscoverySummary:
    considered: int = 0
    accepted: int = 0
    quarantined: int = 0
    races_bound: list[str] = field(default_factory=list)
    quarantined_examples: list[dict] = field(default_factory=list)
    provider_status: str = "OK"

    def as_dict(self) -> dict:
        return {
            "considered": self.considered,
            "accepted": self.accepted,
            "quarantined": self.quarantined,
            "races_bound": self.races_bound,
            "quarantined_examples": self.quarantined_examples[:10],
            "provider_status": self.provider_status,
        }


def _upsert_market(session: Session, dm: DiscoveredMarket) -> Market | None:
    ref = dm.platform_market_id or dm.slug
    if not ref:
        return None
    row = session.execute(
        select(Market).where(
            (Market.platform_market_id == str(ref)) | (Market.slug == (dm.slug or "\0"))
        )
    ).scalars().first()
    if row is None:
        row = Market(platform="polymarket", platform_market_id=str(dm.platform_market_id or dm.slug))
        session.add(row)
    row.slug = dm.slug or row.slug
    row.question = dm.question or row.question
    row.description = dm.description or row.description
    row.category = row.category or "politics"
    row.region = row.region or "US"
    session.flush()
    return row


def _upsert_race_from_hint(session: Session, hint: dict, market: Market | None) -> Race:
    race_id = hint["race_id"]
    row = session.execute(select(Race).where(Race.race_id == race_id)).scalar_one_or_none()
    if row is None:
        row = Race(race_id=race_id)
        session.add(row)
    row.state = str(hint["state"]).upper()[:2]
    row.office = hint["office"]
    row.cycle = int(hint["cycle"])
    if row.election_date is None:
        row.election_date = date(int(hint["cycle"]), 11, 3)
    row.adapter_type = "statewide_race"
    if market is not None:
        row.polymarket_market_id = market.id
        row.polymarket_market_ref = market.platform_market_id or market.slug
    if hint.get("yes_party") in ("DEM", "REP"):
        row.market_yes_party = hint["yes_party"]
    row.contract_yes_party = row.contract_yes_party or "DEM"
    row.source = "market_discovery"
    row.status = "ACTIVE"
    session.flush()
    return row


def discover_and_bind(
    session: Session,
    *,
    discovery_provider: PolymarketDiscoveryProvider | None = None,
    min_confidence: float | None = None,
    job_run_id: int | None = None,
    allow_cache: bool = True,
) -> DiscoverySummary:
    provider = discovery_provider or PolymarketDiscoveryProvider()
    threshold = min_confidence if min_confidence is not None else get_settings().market_classify_min_confidence
    summary = DiscoverySummary()

    res = provider.fetch(session, allow_cache=allow_cache)
    if not res.usable:
        summary.provider_status = res.status
        return summary

    now = datetime.now(timezone.utc)
    for dm in res.normalized_payload:
        cls = dm.classification
        if cls is None:
            continue
        summary.considered += 1
        ref = dm.platform_market_id or dm.slug or dm.question[:120]

        accepted = cls.category == SUPPORTED_STATEWIDE_RACE and cls.race_hint and cls.confidence >= threshold
        market_row = _upsert_market(session, dm) if accepted else None
        race_id = None
        if accepted:
            race = _upsert_race_from_hint(session, cls.race_hint, market_row)
            race_id = race.race_id
            summary.accepted += 1
            summary.races_bound.append(race_id)
        else:
            summary.quarantined += 1
            if len(summary.quarantined_examples) < 10:
                summary.quarantined_examples.append(
                    {"question": dm.question[:160], "category": cls.category, "confidence": cls.confidence,
                     "rationale": cls.rationale}
                )

        session.add(
            MarketClassification(
                market_ref=str(ref),
                market_id=market_row.id if market_row is not None else None,
                question=dm.question,
                category=cls.category,
                confidence=cls.confidence,
                method=cls.method,
                rationale=cls.rationale,
                race_id=race_id,
                race_hint_json=json.dumps(cls.race_hint) if cls.race_hint else None,
                status="ACCEPTED" if accepted else "QUARANTINED",
                classified_at=now,
                job_run_id=job_run_id,
            )
        )
    session.flush()
    return summary
