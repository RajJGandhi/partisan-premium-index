from __future__ import annotations

from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings


class KalshiClient:
    """Small public-data wrapper. Auth is intentionally optional for MVP fallback mode."""

    def __init__(self, base_url: str | None = None, timeout: int = 20):
        settings = get_settings()
        self.base_url = (base_url or settings.kalshi_base_url).rstrip("/")
        self.timeout = timeout
        self.api_key = settings.kalshi_api_key

    @retry(wait=wait_exponential(multiplier=1, min=1, max=12), stop=stop_after_attempt(3), reraise=True)
    def _get(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = requests.get(f"{self.base_url}{path}", params=params or {}, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response

    def fetch_markets(self, limit: int = 100) -> dict[str, Any]:
        return self._get("/markets", {"limit": limit}).json()

    def fetch_orderbook(self, ticker: str) -> dict[str, Any]:
        return self._get(f"/markets/{ticker}/orderbook").json()
