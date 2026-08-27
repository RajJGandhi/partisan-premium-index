"""Prediction-market contamination filter for web evidence (spec section 21).

Web evidence handed to the blind LLM forecasters must not leak prediction-market prices. This
scanner blocks or quarantines any document that originates from -- or substantively discusses --
Polymarket, Kalshi, PredictIt, Manifold, betting exchanges, election-betting odds, or
prediction-market aggregators. If contamination cannot be confidently removed, the document is not
used in a blind forecast.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

CLEAN = "CLEAN"
QUARANTINED = "QUARANTINED"  # mentions markets but might be salvageable with redaction
BLOCKED = "BLOCKED"          # originates from a prediction market / betting source

BLOCKED_DOMAINS: frozenset[str] = frozenset(
    {
        "polymarket.com",
        "kalshi.com",
        "predictit.org",
        "manifold.markets",
        "smarkets.com",
        "betfair.com",
        "predictionmarket.com",
        "electionbettingodds.com",
        "insidethebets.com",
        "oddschecker.com",
        "metaculus.com",  # forecasting aggregator -- keep out of the *blind* packet
        "adjacentnews.com",
        "election-bettingodds.com",
    }
)

# Phrases that indicate the document is *about* market/betting odds, not just politics.
_MARKET_PHRASES = [
    r"\bpolymarket\b",
    r"\bkalshi\b",
    r"\bpredictit\b",
    r"\bmanifold\s+markets?\b",
    r"\bprediction\s+markets?\b",
    r"\bbetting\s+markets?\b",
    r"\bbetting\s+odds\b",
    r"\belection\s+odds\b",
    r"\bimplied\s+probabilit(y|ies)\b",
    r"\bmarket[- ]implied\b",
    r"\bwager(s|ing)?\b\s+(on|that)\b",
    r"\bbookmaker(s)?\b",
    r"\bmoneyline\b",
    r"\bcontract\s+is\s+trading\s+at\b",
    r"\bshares?\s+(are\s+)?trading\s+at\b",
    r"\b\d{1,3}\s*(cents|¢)\s+(on|to)\b",
]
_MARKET_RE = re.compile("|".join(_MARKET_PHRASES), re.IGNORECASE)

# Explicit instruction text for the web-search providers (spec section 21).
SEARCH_PROMPT_PROHIBITION = (
    "Do NOT look for, cite, or return prediction-market odds, betting odds, or "
    "prediction-market probabilities (Polymarket, Kalshi, PredictIt, Manifold, betting "
    "exchanges, or aggregators of these). Report only factual reporting from official "
    "election authorities, government sources, and mainstream news."
)


@dataclass(frozen=True)
class ContaminationResult:
    status: str  # CLEAN / QUARANTINED / BLOCKED
    reason: str | None
    blocked_source: str | None
    hits: tuple[str, ...] = ()

    @property
    def usable_for_blind_forecast(self) -> bool:
        return self.status == CLEAN


def _registrable_domain(host: str) -> str:
    host = host.lower().strip().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


class PredictionMarketContaminationScanner:
    def __init__(self, extra_blocked_domains: set[str] | None = None):
        self.blocked_domains = set(BLOCKED_DOMAINS) | {
            _registrable_domain(d) for d in (extra_blocked_domains or set())
        }

    def scan(self, *, text: str | None = None, url: str | None = None, title: str | None = None) -> ContaminationResult:
        # 1. source domain
        if url:
            host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
            dom = _registrable_domain(host)
            if dom in self.blocked_domains:
                return ContaminationResult(
                    status=BLOCKED,
                    reason=f"source domain '{dom}' is a prediction-market / betting source",
                    blocked_source=dom,
                )

        # 2. content phrases
        blob = " ".join(p for p in (title, text) if p)
        hits = sorted({m.group(0).lower() for m in _MARKET_RE.finditer(blob)})
        if hits:
            return ContaminationResult(
                status=QUARANTINED,
                reason="document references prediction-market / betting odds: " + ", ".join(hits),
                blocked_source=None,
                hits=tuple(hits),
            )
        return ContaminationResult(status=CLEAN, reason=None, blocked_source=None)
