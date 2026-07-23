from __future__ import annotations

from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings


class PredictItClient:
    def __init__(self, url: str | None = None, timeout: int = 20):
        settings = get_settings()
        self.url = url or settings.predictit_marketdata_url
        self.timeout = timeout

    @retry(wait=wait_exponential(multiplier=1, min=1, max=12), stop=stop_after_attempt(3), reraise=True)
    def fetch_all_market_data(self) -> dict[str, Any]:
        response = requests.get(self.url, timeout=self.timeout)
        response.raise_for_status()
        return response.json()
