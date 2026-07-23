from __future__ import annotations

import csv
import json
from pathlib import Path

REQUIRED_SOURCE_PACK_KEYS = {
    "queries",
    "aliases",
    "resolution_criteria",
    "polling_sources",
    "forecast_or_fundamentals_sources",
    "comparable_external_market_sources",
    "expert_or_race_rating_sources",
    "official_sources",
    "news_queries",
    "preferred_domains",
}


def test_production_seed_has_curated_universe_and_complete_source_packs():
    rows = list(csv.DictReader(Path("data/seed/markets.csv").open(encoding="utf-8")))
    packs = json.loads(Path("data/seed/source_packs.json").read_text(encoding="utf-8"))
    assert 10 <= len(rows) <= 20
    assert {row["tracking_id"] for row in rows} == set(packs)
    for row in rows:
        assert row["gamma_market_id"]
        assert row["yes_token_id"]
        assert row["no_token_id"]
        assert REQUIRED_SOURCE_PACK_KEYS <= set(packs[row["tracking_id"]])
        assert packs[row["tracking_id"]]["resolution_criteria"]
