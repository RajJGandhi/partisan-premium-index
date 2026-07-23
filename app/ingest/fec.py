from __future__ import annotations

from typing import Any

import requests

from app.config import get_settings


class FECClient:
    """Optional FEC wrapper. Disabled unless FEC_API_KEY is configured."""

    def __init__(self, timeout: int = 20):
        settings = get_settings()
        self.api_key = settings.fec_api_key
        self.timeout = timeout
        self.base_url = "https://api.open.fec.gov/v1"

    def enabled(self) -> bool:
        return bool(self.api_key)

    def search_candidates(self, query: str, cycle: int | None = None) -> dict[str, Any]:
        if not self.enabled():
            return {"enabled": False, "results": []}
        params: dict[str, Any] = {"api_key": self.api_key, "q": query}
        if cycle:
            params["cycle"] = cycle
        response = requests.get(f"{self.base_url}/candidates/search/", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()
