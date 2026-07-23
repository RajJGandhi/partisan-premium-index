from __future__ import annotations

from typing import Any

import requests

from app.config import get_settings


class GDELTClient:
    def __init__(self, timeout: int = 20):
        self.enabled_flag = get_settings().gdelt_enabled
        self.timeout = timeout
        self.base_url = "https://api.gdeltproject.org/api/v2/doc/doc"

    def enabled(self) -> bool:
        return bool(self.enabled_flag)

    def search_headlines(self, query: str, max_records: int = 10) -> list[str]:
        if not self.enabled():
            return []
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": max_records,
            "sort": "hybridrel",
        }
        response = requests.get(self.base_url, params=params, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        return [article.get("title", "") for article in data.get("articles", []) if article.get("title")]
