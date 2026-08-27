from __future__ import annotations

from app.db.models import Market
from app.db.models_quant import MarketClassification, Race
from app.pipeline_v15.market_discovery import discover_and_bind
from app.providers.markets import (
    DiscoveredMarket,
    PolymarketDiscoveryProvider,
    classify_with_fallback,
)


class _FakeDiscovery(PolymarketDiscoveryProvider):
    QUESTIONS = [
        ("Will the Democratic candidate win the North Carolina U.S. Senate race in 2026?", "nc-sen-1"),
        ("Will the Republican win the 2026 Michigan gubernatorial election?", "mi-gov-1"),
        ("Will the Republican Party control the Senate after the 2026 Midterm elections?", "sen-ctrl-1"),
        ("Will Donald Trump pardon himself in 2026?", "pardon-1"),
        ("Will the 2026 Senate race be close?", "vague-1"),
    ]

    def _do_fetch(self, **kwargs):
        payloads = []
        for q, ref in self.QUESTIONS:
            payloads.append({"question": q, "description": "", "gamma_market_id": ref,
                             "exact_polymarket_slug": ref, "tags_json": []})
        return payloads, "https://gamma-api.polymarket.com/events", 200

    def _normalize(self, raw, **kwargs):
        out = []
        for p in raw:
            out.append(DiscoveredMarket(
                platform_market_id=p["gamma_market_id"], slug=p["exact_polymarket_slug"],
                question=p["question"], description="", tags=[],
                classification=classify_with_fallback(p["question"], "", []),
                raw=p,
            ))
        return out


def test_accepts_statewide_races_and_quarantines_the_rest(quant_db):
    with quant_db() as s:
        summary = discover_and_bind(s, discovery_provider=_FakeDiscovery(backoff_base_seconds=0),
                                    min_confidence=0.8)
        s.commit()
        assert summary.considered == 5
        assert summary.accepted == 2  # the NC senate + MI governor races
        assert summary.quarantined == 3  # senate control, pardon, vague
        assert set(summary.races_bound) == {"nc-sen-2026", "mi-gov-2026"}

        races = {r.race_id: r for r in s.query(Race)}
        assert races["nc-sen-2026"].market_yes_party == "DEM"
        assert races["mi-gov-2026"].market_yes_party == "REP"
        assert races["nc-sen-2026"].polymarket_market_id is not None
        assert s.query(Market).count() == 2  # only accepted contracts get a markets row

        cls = {c.market_ref: c for c in s.query(MarketClassification)}
        assert cls["nc-sen-1"].status == "ACCEPTED" and cls["nc-sen-1"].race_id == "nc-sen-2026"
        assert cls["sen-ctrl-1"].status == "QUARANTINED"
        assert cls["pardon-1"].category == "UNSUPPORTED"
        assert cls["vague-1"].category == "AMBIGUOUS"


def test_below_threshold_statewide_is_quarantined(quant_db):
    with quant_db() as s:
        # a race hint with 0.92 confidence; raise the bar above it
        summary = discover_and_bind(s, discovery_provider=_FakeDiscovery(backoff_base_seconds=0),
                                    min_confidence=0.99)
        s.commit()
        assert summary.accepted == 0
        assert summary.quarantined == 5
        assert s.query(Race).count() == 0


def test_provider_failure_is_reported_not_raised(quant_db):
    class _Broken(_FakeDiscovery):
        def _do_fetch(self, **kwargs):
            from app.providers.base import ProviderError

            raise ProviderError("gamma 503")

    with quant_db() as s:
        summary = discover_and_bind(s, discovery_provider=_Broken(backoff_base_seconds=0))
        assert summary.considered == 0
        assert summary.provider_status in {"FAILED", "EMPTY"}
