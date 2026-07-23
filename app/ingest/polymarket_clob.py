from __future__ import annotations

import json
from typing import Any, Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_clob_token_ids(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if x]
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(x) for x in parsed if x]
        if isinstance(parsed, dict):
            return [str(x) for x in parsed.values() if x]
    except Exception:
        return [part.strip() for part in str(value).split(",") if part.strip()]
    return []


def normalize_book_side(levels: Any, reverse: bool = False) -> list[tuple[float, float]]:
    if not levels:
        return []
    normalized: list[tuple[float, float]] = []
    for level in levels:
        if isinstance(level, dict):
            price = safe_float(level.get("price") or level.get("p"))
            size = safe_float(level.get("size") or level.get("s") or level.get("quantity"))
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            price = safe_float(level[0])
            size = safe_float(level[1])
        else:
            continue
        if price is not None and size is not None:
            normalized.append((price, size))
    normalized.sort(key=lambda x: x[0], reverse=reverse)
    return normalized


def depth_within(levels: list[tuple[float, float]], anchor: float | None, cents: float) -> float | None:
    if anchor is None:
        return None
    bound = anchor + cents
    total = 0.0
    for price, size in levels:
        if price <= bound:
            total += price * size
    return total


class PolymarketCLOBClient:
    def __init__(self, base_url: str | None = None, timeout: int = 20):
        settings = get_settings()
        self.base_url = (base_url or settings.polymarket_clob_base_url).rstrip("/")
        self.timeout = timeout

    @retry(wait=wait_exponential(multiplier=1, min=1, max=12), stop=stop_after_attempt(3), reraise=True)
    def _get(self, path: str, params: dict[str, Any]) -> requests.Response:
        response = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response

    def fetch_book(self, token_id: str) -> dict[str, Any]:
        # Polymarket CLOB public endpoints have used /book?token_id=...; keep this isolated for easy updates.
        response = self._get("/book", {"token_id": token_id})
        return response.json()


def summarize_book(book: dict[str, Any]) -> dict[str, Any]:
    bids = normalize_book_side(book.get("bids"), reverse=True)
    asks = normalize_book_side(book.get("asks"), reverse=False)
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    midpoint = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
    spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "midpoint": midpoint,
        "spread": spread,
        "depth_1c": depth_within(asks, best_ask, 0.01),
        "depth_3c": depth_within(asks, best_ask, 0.03),
        "depth_5c": depth_within(asks, best_ask, 0.05),
        "raw": book,
    }


def summarize_yes_no_books(yes_book: dict[str, Any] | None, no_book: dict[str, Any] | None) -> dict[str, Any]:
    yes = summarize_book(yes_book or {})
    no = summarize_book(no_book or {}) if no_book else {}
    spread = yes.get("spread")
    if spread is None and yes.get("best_bid") is not None and yes.get("best_ask") is not None:
        spread = yes["best_ask"] - yes["best_bid"]
    return {
        "yes_best_bid": yes.get("best_bid"),
        "yes_best_ask": yes.get("best_ask"),
        "no_best_bid": no.get("best_bid"),
        "no_best_ask": no.get("best_ask"),
        "yes_midpoint": yes.get("midpoint"),
        "no_midpoint": no.get("midpoint"),
        "spread": spread,
        "depth_1c": yes.get("depth_1c"),
        "depth_3c": yes.get("depth_3c"),
        "depth_5c": yes.get("depth_5c"),
        "raw_orderbook_json": json.dumps({"yes": yes_book, "no": no_book}, default=str),
    }
