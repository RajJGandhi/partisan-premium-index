from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings

POLITICAL_KEYWORDS = [
    "election",
    "president",
    "senate",
    "house",
    "congress",
    "governor",
    "mayor",
    "trump",
    "biden",
    "harris",
    "republican",
    "democrat",
    "gop",
    "dem",
    "government",
    "court",
    "supreme court",
    "law",
    "bill",
    "policy",
    "tariff",
    "immigration",
    "deportation",
    "pardon",
    "indictment",
    "conviction",
    "resign",
    "appointed",
    "cabinet",
    "fed",
    "sec",
    "cftc",
    "crypto regulation",
    "ukraine",
    "russia",
    "israel",
    "gaza",
    "china",
    "canada election",
    "france election",
    "brazil election",
]


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def normalize_bool(value: Any, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes", "y"}
    return bool(value)


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def json_field(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        # Preserve JSON strings if already encoded; otherwise quote the string.
        try:
            json.loads(value)
            return value
        except Exception:
            return json.dumps(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def tags_to_text(tags: Any) -> str:
    if not tags:
        return ""
    if isinstance(tags, str):
        return tags
    parts: list[str] = []
    for tag in tags if isinstance(tags, list) else [tags]:
        if isinstance(tag, dict):
            parts.extend(str(tag.get(k, "")) for k in ("label", "name", "slug") if tag.get(k))
        else:
            parts.append(str(tag))
    return " ".join(parts)


def is_political_market(market: dict[str, Any], event: dict[str, Any] | None = None) -> bool:
    event = event or {}
    text = " ".join(
        str(x or "")
        for x in [
            market.get("question"),
            market.get("description"),
            market.get("slug"),
            market.get("category"),
            tags_to_text(market.get("tags")),
            tags_to_text(event.get("tags")),
            event.get("title"),
            event.get("slug"),
            event.get("category"),
        ]
    ).lower()
    return any(keyword in text for keyword in POLITICAL_KEYWORDS)


class PolymarketGammaClient:
    def __init__(self, base_url: str | None = None, timeout: int = 20):
        settings = get_settings()
        self.base_url = (base_url or settings.polymarket_gamma_base_url).rstrip("/")
        self.timeout = timeout

    @retry(wait=wait_exponential(multiplier=1, min=1, max=12), stop=stop_after_attempt(3), reraise=True)
    def _get(self, path: str, params: dict[str, Any]) -> requests.Response:
        response = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response

    def fetch_active_events(self, limit: int = 100, max_pages: int | None = None) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        offset = 0
        pages = 0
        while True:
            params = {"active": "true", "closed": "false", "limit": limit, "offset": offset}
            response = self._get("/events", params=params)
            data = response.json()
            if not isinstance(data, list):
                break
            events.extend(data)
            pages += 1
            if len(data) < limit:
                break
            if max_pages is not None and pages >= max_pages:
                break
            offset += limit
        return events


def extract_markets_from_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    markets = event.get("markets") or []
    if not isinstance(markets, list):
        return []
    return markets


def market_payload_from_gamma(market: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    platform_market_id = str(market.get("id") or market.get("conditionId") or market.get("slug") or "")
    if not platform_market_id:
        raise ValueError("Market is missing id/conditionId/slug")
    event_id = event.get("id") or event.get("eventId") or event.get("slug")
    enable_order_book = market.get("enableOrderBook", market.get("enable_order_book"))
    outcomes = market.get("outcomes")
    prices = market.get("outcomePrices") or market.get("outcome_prices")
    clob_ids = market.get("clobTokenIds") or market.get("clob_token_ids")
    return {
        "platform": "polymarket",
        "platform_market_id": platform_market_id,
        "event_id": str(event_id) if event_id is not None else None,
        "condition_id": market.get("conditionId") or market.get("condition_id"),
        "slug": market.get("slug"),
        "question": market.get("question") or event.get("title") or platform_market_id,
        "description": market.get("description") or event.get("description"),
        "rules": market.get("rules") or market.get("resolutionCriteria") or event.get("rules"),
        "resolution_source": market.get("resolutionSource") or market.get("resolution_source"),
        "outcomes_json": json_field(outcomes),
        "clob_token_ids_json": json_field(clob_ids),
        "category": market.get("category") or event.get("category"),
        "tags_json": json_field(market.get("tags") or event.get("tags")),
        "end_date": parse_dt(market.get("endDate") or market.get("end_date") or event.get("endDate")),
        "active": bool(normalize_bool(market.get("active"), True)),
        "closed": bool(normalize_bool(market.get("closed"), False)),
        "enable_order_book": normalize_bool(enable_order_book, None),
        "volume": safe_float(market.get("volume") or market.get("volumeNum")),
        "liquidity": safe_float(market.get("liquidity") or market.get("liquidityNum")),
        "created_at": parse_dt(market.get("createdAt") or market.get("created_at")),
        "updated_at": parse_dt(market.get("updatedAt") or market.get("updated_at")),
        "raw_json": json.dumps({"event": event, "market": market}, ensure_ascii=False, default=str),
    }


def extract_displayed_prices(market: dict[str, Any]) -> tuple[float | None, float | None]:
    prices = market.get("outcomePrices") or market.get("outcome_prices")
    outcomes = market.get("outcomes") or []
    try:
        if isinstance(prices, str):
            prices = json.loads(prices)
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
    except Exception:
        return None, None
    if not isinstance(prices, list):
        return None, None
    yes = safe_float(prices[0]) if prices else None
    no = safe_float(prices[1]) if len(prices) > 1 else (1 - yes if yes is not None else None)
    return yes, no


def iter_relevant_market_payloads(events: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for event in events:
        for market in extract_markets_from_event(event):
            if not is_political_market(market, event):
                continue
            active = normalize_bool(market.get("active"), True)
            closed = normalize_bool(market.get("closed"), False)
            enable = normalize_bool(market.get("enableOrderBook", market.get("enable_order_book")), True)
            if active is False or closed is True or enable is False:
                continue
            yield market_payload_from_gamma(market, event)
