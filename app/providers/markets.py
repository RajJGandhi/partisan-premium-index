"""Automatic Polymarket market discovery + classification (spec section 42).

Discovery pulls candidate political markets from Polymarket Gamma. Classification maps each onto:

    SUPPORTED_STATEWIDE_RACE | SUPPORTED_SENATE_CONTROL | SUPPORTED_HOUSE_CONTROL
    | UNSUPPORTED | AMBIGUOUS

Deterministic regex parsing runs first; an LLM classifier is a fallback hook only. A market is
auto-published only when its mapping confidence clears ``market_classify_min_confidence``;
``AMBIGUOUS`` (and low-confidence) markets go to a quarantine status rather than getting a
fabricated forecast. This module never reads a market *price* -- only question / description / tags.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.config import get_settings
from app.providers.base import BaseProvider, ProviderError
from app.providers.normalize import canonical_race_id

MARKET_DISCOVERY_KIND = "market_discovery"

SUPPORTED_STATEWIDE_RACE = "SUPPORTED_STATEWIDE_RACE"
SUPPORTED_SENATE_CONTROL = "SUPPORTED_SENATE_CONTROL"
SUPPORTED_HOUSE_CONTROL = "SUPPORTED_HOUSE_CONTROL"
UNSUPPORTED = "UNSUPPORTED"
AMBIGUOUS = "AMBIGUOUS"

_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

_UNSUPPORTED_PATTERNS = [
    r"\bpardon\b", r"\bindict", r"\bconvict", r"\bimpeach", r"\bresign\b", r"\bcabinet\b",
    r"\bapproval rating\b", r"\bfavorability\b", r"\bshut ?down\b", r"\bdebt ceiling\b",
    r"\btweet\b|\bpost on (x|truth)\b", r"\bnominee for\b.*\b(court|fed|secretary)\b",
    r"\brecession\b", r"\bballot measure\b|\bproposition \d+\b", r"\bprimary\b",
    r"\bspeaker of the house\b", r"\bmeet with\b", r"\bvisit\b",
]
_UNSUPPORTED_RE = re.compile("|".join(_UNSUPPORTED_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class MarketClassification:
    category: str
    confidence: float
    rationale: str
    race_hint: Optional[dict] = None  # {"state","office","cycle","race_id"} for statewide
    method: str = "deterministic"

    @property
    def is_supported(self) -> bool:
        return self.category.startswith("SUPPORTED_")

    def auto_publishable(self, min_confidence: float | None = None) -> bool:
        thr = min_confidence if min_confidence is not None else get_settings().market_classify_min_confidence
        return self.is_supported and self.confidence >= thr


def _year(text: str) -> Optional[int]:
    m = re.search(r"\b(20\d\d)\b", text)
    return int(m.group(1)) if m else None


def _yes_party(low_text: str) -> Optional[str]:
    """Which party winning = the contract resolving YES, from the question wording. ``None`` if
    the wording does not name a party (e.g. "Which party wins ...") -- the caller then leaves
    ``races.market_yes_party`` unset and the market series is excluded from scoring for that race."""
    dem = re.search(r"\b(democrats?|democratic|dem)\b", low_text)
    rep = re.search(r"\b(republicans?|gop)\b", low_text)
    if dem and not rep:
        return "DEM"
    if rep and not dem:
        return "REP"
    return None


def _find_state(text: str) -> Optional[str]:
    low = text.lower()
    for name, abbr in _STATES.items():
        if re.search(rf"\b{re.escape(name)}\b", low):
            return abbr
    m = re.search(r"\b([A-Z]{2})\b(?=\s+(senate|governor|gubernatorial|u\.s\. senate))", text)
    if m and m.group(1) in _STATES.values():
        return m.group(1)
    return None


def classify_market(question: str, description: str = "", tags: Any = ()) -> MarketClassification:
    """Deterministic classification of a Polymarket political contract."""
    q = (question or "").strip()
    text = " ".join([q, description or "", " ".join(map(str, tags or []))]).strip()
    low = text.lower()
    year = _year(text) or _year(q)

    # chamber control
    senate_ctrl = re.search(r"\bsenate\b", low) and re.search(r"\b(control|majority|flip|hold)\b", low)
    house_ctrl = re.search(r"\bhouse\b", low) and re.search(r"\b(control|majority|flip|hold)\b", low)
    if senate_ctrl and not re.search(r"\b(race|seat|election in)\b", low):
        return MarketClassification(
            SUPPORTED_SENATE_CONTROL,
            0.95 if year else 0.85,
            f"question is about Senate chamber control{f' ({year})' if year else ''}",
        )
    if house_ctrl and not re.search(r"\b(race|seat|district)\b", low):
        return MarketClassification(
            SUPPORTED_HOUSE_CONTROL,
            0.9 if year else 0.8,
            "question is about House chamber control (Quant adapter is UNAVAILABLE; market + blind LLM only)",
        )

    # statewide race
    office = None
    if re.search(r"\b(u\.?s\.? )?senate\b", low) and re.search(r"\b(race|seat|election|win|elect)\b", low):
        office = "senate"
    elif re.search(r"\b(governor|gubernatorial)\b", low):
        office = "governor"
    if office:
        state = _find_state(text)
        if state and year:
            rid = canonical_race_id(state, office, year)
            return MarketClassification(
                SUPPORTED_STATEWIDE_RACE,
                0.92,
                f"{state} {office} general election {year}",
                race_hint={
                    "state": state,
                    "office": office,
                    "cycle": year,
                    "race_id": rid,
                    "yes_party": _yes_party(low),  # which party the contract's YES side names
                },
            )
        if office and (state or year):
            return MarketClassification(
                AMBIGUOUS, 0.5,
                f"looks like a {office} race but missing {'state' if not state else 'year'}",
            )

    if _UNSUPPORTED_RE.search(text):
        return MarketClassification(UNSUPPORTED, 0.9, "matches an unsupported political-contract pattern")

    if re.search(r"\b(election|senate|house|governor|congress|midterm)\b", low):
        return MarketClassification(
            AMBIGUOUS, 0.4, "political but does not map cleanly onto a supported forecasting adapter"
        )
    return MarketClassification(UNSUPPORTED, 0.8, "not an election-outcome contract")


LLMMarketClassifier = Callable[[str, str], MarketClassification]


def classify_with_fallback(
    question: str,
    description: str = "",
    tags: Any = (),
    *,
    llm_classifier: Optional[LLMMarketClassifier] = None,
    min_confidence: float | None = None,
) -> MarketClassification:
    det = classify_market(question, description, tags)
    thr = min_confidence if min_confidence is not None else get_settings().market_classify_min_confidence
    if det.category != AMBIGUOUS and det.confidence >= thr:
        return det
    if llm_classifier is not None:
        try:
            llm = llm_classifier(question, description)
        except Exception as exc:  # a classifier failure must not fabricate a category
            return MarketClassification(AMBIGUOUS, det.confidence, f"LLM classifier errored: {exc}", det.race_hint, "llm")
        if llm.category != AMBIGUOUS and llm.confidence >= thr:
            return MarketClassification(llm.category, llm.confidence, llm.rationale, llm.race_hint, "llm")
    return det


@dataclass
class DiscoveredMarket:
    platform_market_id: Optional[str]
    slug: Optional[str]
    question: str
    description: str
    tags: list[str] = field(default_factory=list)
    end_date: Optional[str] = None
    classification: Optional[MarketClassification] = None
    raw: dict = field(default_factory=dict)


class PolymarketDiscoveryProvider(BaseProvider):
    """Pulls candidate political markets from Polymarket Gamma and classifies each."""

    name = "polymarket_gamma_discovery"
    kind = MARKET_DISCOVERY_KIND
    endpoint_family = "polymarket:gamma_events"

    def __init__(self, *, event_limit: int = 200, max_pages: int | None = 4, llm_classifier=None, **kw):
        super().__init__(**kw)
        self.event_limit = event_limit
        self.max_pages = max_pages
        self._llm = llm_classifier

    def _cache_params(self, **kwargs) -> dict:
        return {"limit": self.event_limit, "pages": self.max_pages}

    def _do_fetch(self, **kwargs) -> tuple[Any, str | None, int | None]:
        from app.ingest.polymarket_gamma import (
            PolymarketGammaClient,
            iter_relevant_market_payloads,
        )

        try:
            client = PolymarketGammaClient(timeout=self.timeout)
            events = client.fetch_active_events(limit=self.event_limit, max_pages=self.max_pages)
            payloads = list(iter_relevant_market_payloads(events))
        except Exception as exc:
            raise ProviderError(f"{self.name}: Gamma discovery failed: {exc}") from exc
        return payloads, "https://gamma-api.polymarket.com/events", 200

    def _normalize(self, raw: Any, **kwargs) -> list[DiscoveredMarket]:
        out: list[DiscoveredMarket] = []
        for p in raw if isinstance(raw, list) else []:
            if not isinstance(p, dict):
                continue
            tags = p.get("tags") or p.get("tags_json") or []
            if isinstance(tags, str):
                tags = [tags]
            question = str(p.get("question") or p.get("market_name") or "")
            description = str(p.get("description") or p.get("description_text") or "")
            cls = classify_with_fallback(question, description, tags, llm_classifier=self._llm)
            out.append(
                DiscoveredMarket(
                    platform_market_id=str(p.get("gamma_market_id") or p.get("id") or "") or None,
                    slug=p.get("exact_polymarket_slug") or p.get("slug"),
                    question=question,
                    description=description,
                    tags=[str(t) for t in tags],
                    end_date=str(p.get("end_date") or p.get("endDate") or "") or None,
                    classification=cls,
                    raw=p,
                )
            )
        return out
