from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import requests
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.db.models import Market, RawMarketResponse
from app.ingest.polymarket_clob import PolymarketCLOBClient, summarize_yes_no_books
from app.ingest.polymarket_gamma import json_field, parse_dt, safe_float


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class TrackedPolymarketClient:
    def __init__(self):
        settings = get_settings()
        self.gamma = settings.polymarket_gamma_base_url.rstrip("/")
        self.timeout = settings.source_timeout_seconds
        self.clob = PolymarketCLOBClient(settings.polymarket_clob_base_url, timeout=self.timeout)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=12), stop=stop_after_attempt(3), reraise=True)
    def fetch_market(self, market: Market) -> tuple[dict[str, Any], str, int]:
        if market.platform_market_id and str(market.platform_market_id).isdigit():
            endpoint = f"{self.gamma}/markets/{market.platform_market_id}"
        elif market.slug:
            endpoint = f"{self.gamma}/markets/slug/{market.slug}"
        else:
            raise ValueError("Tracked market has neither a Gamma market ID nor slug")
        response = requests.get(endpoint, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected Gamma response shape")
        return data, endpoint, response.status_code

    def fetch_order_books(self, market: Market) -> dict[str, Any]:
        yes_token = market.yes_token_id
        no_token = market.no_token_id
        if not yes_token:
            try:
                ids = json.loads(market.clob_token_ids_json or "[]")
                if isinstance(ids, list):
                    yes_token = str(ids[0]) if ids else None
                    no_token = str(ids[1]) if len(ids) > 1 else None
            except Exception:
                pass
        if not yes_token:
            raise ValueError("Missing YES token ID")
        yes_book = self.clob.fetch_book(yes_token)
        no_book = self.clob.fetch_book(no_token) if no_token else None
        summary = summarize_yes_no_books(yes_book, no_book)
        summary["last_trade_price"] = safe_float(yes_book.get("last_trade_price") or yes_book.get("lastTradePrice"))
        summary["upstream_timestamp"] = yes_book.get("timestamp")
        return summary


def update_market_from_gamma(market: Market, data: dict[str, Any]) -> None:
    market.platform_market_id = str(data.get("id") or market.platform_market_id)
    market.condition_id = data.get("conditionId") or market.condition_id
    market.slug = data.get("slug") or market.slug
    market.question = data.get("question") or market.question
    market.description = data.get("description") or market.description
    market.rules = data.get("rules") or data.get("resolutionCriteria") or market.rules
    market.resolution_source = data.get("resolutionSource") or market.resolution_source
    market.outcomes_json = json_field(data.get("outcomes")) or market.outcomes_json
    market.clob_token_ids_json = json_field(data.get("clobTokenIds")) or market.clob_token_ids_json
    market.enable_order_book = bool(data.get("enableOrderBook", market.enable_order_book))
    market.active = bool(data.get("active", market.active))
    market.closed = bool(data.get("closed", market.closed))
    market.end_date = parse_dt(data.get("endDate")) or market.end_date
    market.volume = safe_float(data.get("volumeNum") or data.get("volume"))
    market.liquidity = safe_float(data.get("liquidityNum") or data.get("liquidity"))
    market.raw_json = json.dumps(data, ensure_ascii=False, default=str)
    market.last_seen_at = utcnow()
    market.last_market_sync_at = utcnow()
    try:
        ids = json.loads(market.clob_token_ids_json or "[]")
        if isinstance(ids, list):
            market.yes_token_id = str(ids[0]) if ids else market.yes_token_id
            market.no_token_id = str(ids[1]) if len(ids) > 1 else market.no_token_id
    except Exception:
        pass


def save_raw_response(
    session: Session,
    market: Market | None,
    source: str,
    endpoint: str,
    payload: Any | None,
    status_code: int | None,
    error: str | None = None,
    upstream_timestamp: datetime | None = None,
    stale: bool = False,
) -> RawMarketResponse:
    row = RawMarketResponse(
        market_id=market.id if market else None,
        source=source,
        endpoint=endpoint,
        fetched_at=utcnow(),
        upstream_timestamp=upstream_timestamp,
        response_json=json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else None,
        status_code=status_code,
        response_hash=_hash_payload(payload) if payload is not None else None,
        is_stale=stale,
        error_message=error,
    )
    session.add(row)
    session.flush()
    return row


def price_policy(book: dict[str, Any]) -> dict[str, Any]:
    bid = book.get("yes_best_bid")
    ask = book.get("yes_best_ask")
    last = book.get("last_trade_price")
    if bid is not None and ask is not None:
        return {
            "comparison_price": (float(bid) + float(ask)) / 2,
            "price_type": "mid",
            "executable_buy_price": float(ask),
            "executable_sell_price": float(bid),
        }
    if ask is not None:
        return {
            "comparison_price": float(ask),
            "price_type": "ask_only",
            "executable_buy_price": float(ask),
            "executable_sell_price": None,
        }
    if last is not None:
        return {
            "comparison_price": float(last),
            "price_type": "last_trade",
            "executable_buy_price": None,
            "executable_sell_price": None,
        }
    return {
        "comparison_price": None,
        "price_type": "unavailable",
        "executable_buy_price": None,
        "executable_sell_price": None,
    }


def fetch_price_history(
    token_id: str, start_ts: int | None = None, end_ts: int | None = None, fidelity: int = 60
) -> list[dict[str, Any]]:
    settings = get_settings()
    params: dict[str, Any] = {"market": token_id, "fidelity": fidelity}
    if start_ts is not None:
        params["startTs"] = start_ts
    if end_ts is not None:
        params["endTs"] = end_ts
    response = requests.get(
        f"{settings.polymarket_clob_base_url.rstrip('/')}/prices-history",
        params=params,
        timeout=settings.source_timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    history = data.get("history", []) if isinstance(data, dict) else []
    return history if isinstance(history, list) else []
