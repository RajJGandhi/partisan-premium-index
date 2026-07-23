from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import feedparser
from sqlalchemy import select
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.db.models import EvidenceItem, Market, MarketSource
from app.ppi.classifier import classify_with_fallback
from app.ppi.security import canonicalize_url, safe_get, validate_external_url


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_title(title: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", title.lower())
    return " ".join(text.split())


def evidence_hash(title: str, canonical_url: str | None, content: str | None = None) -> str:
    material = "|".join([normalize_title(title), canonical_url or "", (content or "")[:2000]])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        from email.utils import parsedate_to_datetime

        d = parsedate_to_datetime(str(value))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        try:
            d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            return None


@dataclass
class EvidenceCandidate:
    source_type: str
    source_name: str
    title: str
    url: str | None
    published_at: datetime | None = None
    content_text: str | None = None
    raw: dict[str, Any] | None = None


class SourceAdapter:
    def collect(self, market: Market, source: MarketSource) -> list[EvidenceCandidate]:
        raise NotImplementedError


class RSSAdapter(SourceAdapter):
    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def _fetch(self, url: str, allowed_domains: set[str] | None = None) -> bytes:
        settings = get_settings()
        response = safe_get(
            url,
            allowed_domains=allowed_domains,
            timeout=settings.source_timeout_seconds,
            headers={"User-Agent": settings.source_user_agent},
        )
        response.raise_for_status()
        return response.content

    def collect(self, market: Market, source: MarketSource) -> list[EvidenceCandidate]:
        config = json.loads(source.config_json or "{}")
        if source.url:
            url = validate_external_url(source.url, _domains(source))
        else:
            query = source.query or market.question
            url = f"https://news.google.com/rss/search?q={quote_plus(query + ' when:7d')}&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(self._fetch(url, _domains(source) if source.url else None))
        limit = int(config.get("max_items", 12))
        out: list[EvidenceCandidate] = []
        for entry in feed.entries[:limit]:
            link = str(entry.get("link") or "")
            try:
                canonical = validate_external_url(link) if link else None
            except Exception:
                canonical = canonicalize_url(link) if link.startswith("https://news.google.com/") else None
            out.append(
                EvidenceCandidate(
                    source_type="rss",
                    source_name=source.name,
                    title=str(entry.get("title") or "Untitled"),
                    url=canonical,
                    published_at=parse_date(entry.get("published") or entry.get("updated")),
                    content_text=str(entry.get("summary") or ""),
                    raw=dict(entry),
                )
            )
        return out


class GDELTAdapter(SourceAdapter):
    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def collect(self, market: Market, source: MarketSource) -> list[EvidenceCandidate]:
        settings = get_settings()
        if not settings.gdelt_enabled:
            return []
        config = json.loads(source.config_json or "{}")
        query = source.query or market.question
        endpoint = "https://api.gdeltproject.org/api/v2/doc/doc"
        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": int(config.get("max_items", 10)),
            "sort": "DateDesc",
        }
        response = safe_get(
            endpoint,
            params=params,
            timeout=settings.source_timeout_seconds,
            headers={"User-Agent": settings.source_user_agent},
        )
        response.raise_for_status()
        out = []
        for item in response.json().get("articles", []):
            url = item.get("url")
            try:
                url = validate_external_url(url, _domains(source)) if url else None
            except Exception:
                continue
            out.append(
                EvidenceCandidate(
                    "gdelt",
                    source.name,
                    str(item.get("title") or "Untitled"),
                    url,
                    parse_date(item.get("seendate")),
                    raw=item,
                )
            )
        return out


class JSONAPIAdapter(SourceAdapter):
    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3), reraise=True)
    def collect(self, market: Market, source: MarketSource) -> list[EvidenceCandidate]:
        if not source.url:
            return []
        config = json.loads(source.config_json or "{}")
        url = validate_external_url(source.url, _domains(source))
        settings = get_settings()
        response = safe_get(
            url,
            allowed_domains=_domains(source),
            timeout=settings.source_timeout_seconds,
            headers={"User-Agent": settings.source_user_agent},
        )
        response.raise_for_status()
        data = response.json()
        items = data
        for part in str(config.get("items_path", "")).split("."):
            if part:
                items = items.get(part, []) if isinstance(items, dict) else []
        if not isinstance(items, list):
            return []
        out = []
        for item in items[: int(config.get("max_items", 20))]:
            if not isinstance(item, dict):
                continue
            title = str(item.get(config.get("title_field", "title")) or "Untitled")
            item_url = item.get(config.get("url_field", "url"))
            try:
                item_url = validate_external_url(str(item_url), _domains(source)) if item_url else url
            except Exception:
                item_url = None
            out.append(
                EvidenceCandidate(
                    "json_api",
                    source.name,
                    title,
                    item_url,
                    parse_date(item.get(config.get("date_field", "published_at"))),
                    str(item.get(config.get("content_field", "summary")) or ""),
                    item,
                )
            )
        return out


def _domains(source: MarketSource) -> set[str] | None:
    try:
        values = json.loads(source.allowed_domains_json or "[]")
        return {str(x).lower() for x in values} or None
    except Exception:
        return None


def adapter_for(source_type: str) -> SourceAdapter | None:
    return {
        "rss": RSSAdapter(),
        "google_news": RSSAdapter(),
        "gdelt": GDELTAdapter(),
        "json_api": JSONAPIAdapter(),
    }.get(source_type)


def market_aliases(market: Market) -> list[str]:
    try:
        pack = json.loads(market.source_pack_json or "{}")
        return [str(x) for x in pack.get("aliases", [])]
    except Exception:
        return []


def insert_and_classify_candidate(
    session: Session, market: Market, source: MarketSource | None, candidate: EvidenceCandidate
) -> tuple[EvidenceItem, bool]:
    canonical = canonicalize_url(candidate.url) if candidate.url else None
    digest = evidence_hash(candidate.title, canonical, candidate.content_text)
    existing = session.scalar(
        select(EvidenceItem).where(EvidenceItem.market_id == market.id, EvidenceItem.content_hash == digest)
    )
    if existing:
        return existing, False
    classification, fallback_warning = classify_with_fallback(
        {
            "market_question": market.question,
            "resolution_criteria": market.rules or "",
            "aliases": market_aliases(market),
            "title": candidate.title,
            "content_text": candidate.content_text or "",
            "url": canonical or "",
            "source_name": candidate.source_name,
            "published_at": candidate.published_at.isoformat() if candidate.published_at else None,
        }
    )
    provider = get_settings().llm_provider if not fallback_warning else "deterministic_fallback"
    item = EvidenceItem(
        market_id=market.id,
        market_source_id=source.id if source else None,
        source_type=candidate.source_type,
        source_name=candidate.source_name,
        title=candidate.title,
        canonical_url=canonical,
        original_url=candidate.url,
        published_at=candidate.published_at,
        content_text=candidate.content_text,
        normalized_title=normalize_title(candidate.title),
        content_hash=digest,
        raw_json=json.dumps(candidate.raw or {}, ensure_ascii=False, default=str),
        relevant=classification.relevant,
        relevance_score=classification.relevance_score,
        source_quality=classification.source_quality,
        changes_probability=classification.changes_probability,
        direction=classification.direction,
        estimated_magnitude=classification.estimated_magnitude,
        category=classification.category,
        summary=classification.summary,
        reason=classification.reason + ((" " + fallback_warning) if fallback_warning else ""),
        needs_human_review=classification.needs_human_review,
        classifier_provider=provider,
        classifier_model=get_settings().llm_model if provider != "deterministic" else "rules-v1",
        classifier_raw_json=classification.model_dump_json(),
        review_status="PENDING" if classification.needs_human_review else "AUTO_ACCEPTED",
    )
    session.add(item)
    session.flush()
    return item, True
