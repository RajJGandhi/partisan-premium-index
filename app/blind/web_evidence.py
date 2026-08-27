"""Automated web-evidence worker (spec sections 20, 21, 45).

Collects material race-specific news -- candidate withdrawals/replacements, eligibility/legal
changes, election-administration changes, confirmed endorsements, official candidate status --
through an injected web-search callable (OpenAI or Anthropic web search). Every document is run
through the prediction-market :class:`PredictionMarketContaminationScanner`; BLOCKED / QUARANTINED
documents are stored with their status but **excluded** from anything a blind forecaster sees.

This news is displayed, stored, and available to the GPT/Claude blind benchmarks. It is **not** an
input to the deterministic Quant math -- v1 Quant does not let qualitative news move the
probability (spec section 20).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models_quant import RaceNewsItem
from app.providers.contamination import (
    SEARCH_PROMPT_PROHIBITION,
    PredictionMarketContaminationScanner,
)

# search_fn(query: str, *, max_results: int) -> list[dict] with keys: title, url, snippet/summary, published_at
WebSearchFn = Callable[..., list[dict]]

CATEGORY_HINTS = {
    "withdrawal": ("withdraw", "drops out", "suspends campaign", "steps aside", "replace on the ballot"),
    "legal": ("lawsuit", "court", "ballot access", "disqualif", "eligibility", "injunction", "ruling"),
    "administration": ("election administration", "polling place", "mail ballot", "recount", "certif"),
    "endorsement": ("endorse", "endorsement", "backs "),
    "status": ("nominee", "clinches", "wins primary", "unopposed", "files to run"),
}


@dataclass
class NewsItem:
    title: str
    url: Optional[str]
    source_domain: Optional[str]
    published_at: Optional[datetime]
    summary: Optional[str]
    category: Optional[str]
    contamination_status: str
    contamination_reason: Optional[str]
    blocked_source: Optional[str]
    content_hash: str

    @property
    def usable_for_blind_forecast(self) -> bool:
        return self.contamination_status == "CLEAN"

    def as_bundle_entry(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source_domain": self.source_domain,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "summary": self.summary,
            "category": self.category,
        }


def _content_hash(title: str, url: str | None) -> str:
    material = "|".join([" ".join((title or "").lower().split()), (url or "").strip().lower()])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _categorize(text: str) -> Optional[str]:
    low = (text or "").lower()
    for cat, needles in CATEGORY_HINTS.items():
        if any(n in low for n in needles):
            return cat
    return None


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def collect_race_news(
    *,
    race_id: str,
    state: str,
    office: str,
    dem_candidate: str | None,
    rep_candidate: str | None,
    cycle: int,
    search_fn: Optional[WebSearchFn],
    scanner: PredictionMarketContaminationScanner | None = None,
    max_searches: int | None = None,
    as_of: datetime | None = None,
) -> list[NewsItem]:
    """Return contamination-scanned news items. No ``search_fn`` -> ``[]`` (news is optional)."""
    if search_fn is None:
        return []
    scanner = scanner or PredictionMarketContaminationScanner()
    cap = max_searches if max_searches is not None else get_settings().blind_max_web_searches

    queries: list[str] = [
        f"{state} {office} race {cycle} candidate news latest",
        f"{dem_candidate or ''} {rep_candidate or ''} {state} {office} {cycle}".strip(),
        f"{state} {office} {cycle} ballot access OR withdrawal OR endorsement",
    ][:cap]

    seen: set[str] = set()
    items: list[NewsItem] = []
    for q in queries:
        try:
            results = search_fn(query=f"{q}. {SEARCH_PROMPT_PROHIBITION}", max_results=8) or []
        except Exception:  # a search failure must not abort the forecast run
            continue
        for r in results:
            if not isinstance(r, dict):
                continue
            title = str(r.get("title") or "").strip()
            url = r.get("url") or r.get("link")
            if not title:
                continue
            ch = _content_hash(title, url)
            if ch in seen:
                continue
            seen.add(ch)
            summary = r.get("summary") or r.get("snippet") or r.get("content")
            scan = scanner.scan(title=title, text=summary, url=url)
            domain = urlparse(url).hostname if url and "://" in str(url) else (str(url).split("/")[0] if url else None)
            items.append(
                NewsItem(
                    title=title,
                    url=url,
                    source_domain=domain,
                    published_at=_parse_dt(r.get("published_at") or r.get("date")),
                    summary=(summary or "")[:2000] or None,
                    category=_categorize(f"{title} {summary or ''}"),
                    contamination_status=scan.status,
                    contamination_reason=scan.reason,
                    blocked_source=scan.blocked_source,
                    content_hash=ch,
                )
            )
    return items


def persist_news(session: Session, race_id: str, items: list[NewsItem], *, provider: str = "web_search") -> int:
    """Append new news items (dedup on content_hash). Returns the count inserted."""
    n = 0
    for it in items:
        exists = session.execute(
            select(RaceNewsItem.id).where(
                RaceNewsItem.race_id == race_id, RaceNewsItem.content_hash == it.content_hash
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        session.add(
            RaceNewsItem(
                race_id=race_id,
                provider=provider,
                title=it.title,
                url=it.url,
                source_domain=it.source_domain,
                published_at=it.published_at,
                retrieved_at=datetime.now(timezone.utc),
                summary=it.summary,
                category=it.category,
                content_hash=it.content_hash,
                contamination_status=it.contamination_status,
                contamination_reason=it.contamination_reason,
                blocked_source=it.blocked_source,
            )
        )
        n += 1
    session.flush()
    return n


def clean_news_for_bundle(items: list[NewsItem]) -> list[dict]:
    """Only CLEAN items, in the shape ``EvidenceBundle.current_news`` expects."""
    return [it.as_bundle_entry() for it in items if it.usable_for_blind_forecast]
