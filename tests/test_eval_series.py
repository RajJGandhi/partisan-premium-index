from __future__ import annotations

from datetime import date, datetime, timezone

from app.db.models import LLMForecast, Market, MarketSnapshot
from app.db.models_quant import (
    BlindBenchmarkForecast,
    EnsembleForecast,
    QuantForecast,
    Race,
)
from app.eval.series import (
    SERIES_ANTHROPIC,
    SERIES_ENSEMBLE,
    SERIES_LEGACY_LLM,
    SERIES_MARKET,
    SERIES_OPENAI,
    SERIES_QUANT,
    collect_series,
)


def _now(day):
    return datetime(2026, 9, day, 13, tzinfo=timezone.utc)


def _base_race(s, **kw):
    d = dict(race_id="tx-sen-2026", state="TX", office="senate", cycle=2026,
             election_date=date(2026, 11, 3), contract_yes_party="DEM")
    d.update(kw)
    s.add(Race(**d))
    s.flush()


def test_collects_quant_blind_ensemble(quant_db):
    with quant_db() as s:
        _base_race(s)
        s.add(QuantForecast(race_id="tx-sen-2026", run_key="k1", methodology_version="ppi-quant-v1.0",
                            config_hash="c" * 64, input_hash="i" * 64, generated_at=_now(1),
                            data_quality="NORMAL", abstained=False, p_dem_win=0.7, p_dem_win_uncapped=0.7,
                            p_rep_win=0.3, pipeline_mode="shadow", publication_status="SHADOW"))
        s.add(QuantForecast(race_id="tx-sen-2026", run_key="k2-abstain", methodology_version="ppi-quant-v1.0",
                            config_hash="c" * 64, input_hash="i" * 64, generated_at=_now(2),
                            data_quality="ABSTAIN", abstained=True, pipeline_mode="shadow",
                            publication_status="SHADOW"))
        for prov, p in (("openai", 0.66), ("anthropic", 0.61)):
            s.add(BlindBenchmarkForecast(race_id="tx-sen-2026", run_key="k1", provider=prov, model_name="m",
                                         prompt_version="blind_benchmark_v1", methodology_version="ppi-blind-v1",
                                         evidence_bundle_hash="e" * 64, status="OK", probability=p, generated_at=_now(1)))
        s.add(BlindBenchmarkForecast(race_id="tx-sen-2026", run_key="k2", provider="openai", model_name="m",
                                     prompt_version="v", methodology_version="ppi-blind-v1",
                                     evidence_bundle_hash="e2" + "0" * 62, status="FAILED", generated_at=_now(2)))
        s.add(EnsembleForecast(race_id="tx-sen-2026", run_key="k1", methodology_version="ppi-ensemble-v1.5",
                               available=True, ensemble_probability=0.68, weights_json="{}", generated_at=_now(1)))
        s.add(EnsembleForecast(race_id="tx-sen-2026", run_key="k2", methodology_version="ppi-ensemble-v1.5",
                               available=False, weights_json="{}", generated_at=_now(2)))
        s.flush()

        series = collect_series(s, "tx-sen-2026")
        assert [o.probability for o in series[SERIES_QUANT]] == [0.7]  # abstained one excluded
        assert [o.probability for o in series[SERIES_OPENAI]] == [0.66]  # FAILED one excluded
        assert [o.probability for o in series[SERIES_ANTHROPIC]] == [0.61]
        assert [o.probability for o in series[SERIES_ENSEMBLE]] == [0.68]  # unavailable one excluded
        assert series[SERIES_MARKET] == [] and series[SERIES_LEGACY_LLM] == []


def test_market_and_legacy_need_orientation(quant_db):
    with quant_db() as s:
        market = Market(platform_market_id="1", question="Will the Republican win the TX Senate race?")
        s.add(market)
        s.flush()
        # market YES = REP; race contract YES = DEM -> probabilities must be flipped
        _base_race(s, polymarket_market_id=market.id, market_yes_party="REP")
        s.add(MarketSnapshot(market_id=market.id, timestamp=_now(1), comparison_price=0.30, price_type="midpoint"))
        s.add(LLMForecast(market_id=market.id, run_key="r", run_slot="2026-09-01:primary", generated_at=_now(1),
                          model_provider="openrouter", model_name="deepseek", prompt_version="fair_value_v0.1",
                          fair_value=0.28, status="OK", forecast_role="legacy_blind_llm"))
        s.flush()
        series = collect_series(s, "tx-sen-2026")
        assert series[SERIES_MARKET][0].probability == 0.70  # 1 - 0.30
        assert series[SERIES_LEGACY_LLM][0].probability == 0.72  # 1 - 0.28


def test_market_excluded_when_yes_party_unknown(quant_db):
    with quant_db() as s:
        market = Market(platform_market_id="9", question="ambiguous")
        s.add(market)
        s.flush()
        _base_race(s, polymarket_market_id=market.id, market_yes_party=None)
        s.add(MarketSnapshot(market_id=market.id, timestamp=_now(1), comparison_price=0.55))
        s.flush()
        series = collect_series(s, "tx-sen-2026")
        assert series[SERIES_MARKET] == []  # abstain rather than guess a direction
