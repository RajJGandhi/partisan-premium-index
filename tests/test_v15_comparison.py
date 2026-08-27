from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.models import Market, MarketSnapshot
from app.db.models_quant import (
    EnsembleForecast,
    ForecastMarketComparison,
    QuantForecast,
    Race,
)
from app.pipeline_v15.comparison import join_forecasts_with_market

RK = "ppi-v15:2026-08-27:primary"


def _setup(session, *, market_yes_party="DEM", comparison_price=0.72, contract_yes="DEM"):
    m = Market(platform_market_id="42", question="Will the Democrat win the NC Senate race?")
    session.add(m)
    session.flush()
    session.add(Race(race_id="nc-sen-2026", state="NC", office="senate", cycle=2026,
                     election_date=datetime(2026, 11, 3).date(), polymarket_market_id=m.id,
                     market_yes_party=market_yes_party, contract_yes_party=contract_yes))
    session.add(MarketSnapshot(market_id=m.id, timestamp=datetime(2026, 8, 27, 12, tzinfo=timezone.utc),
                               comparison_price=comparison_price, price_type="midpoint", liquidity=50000.0,
                               volume=120000.0))
    session.add(QuantForecast(race_id="nc-sen-2026", run_key=RK, methodology_version="ppi-quant-v1.0",
                              config_hash="c" * 64, input_hash="i" * 64,
                              generated_at=datetime(2026, 8, 27, 13, tzinfo=timezone.utc),
                              data_quality="STRONG", abstained=False, p_dem_win=0.80, p_dem_win_uncapped=0.80,
                              p_rep_win=0.20, pipeline_mode="shadow", publication_status="SHADOW"))
    session.add(EnsembleForecast(race_id="nc-sen-2026", run_key=RK, methodology_version="ppi-ensemble-v1.5",
                                 available=True, ensemble_probability=0.83, quant_probability=0.80,
                                 openai_probability=0.85, anthropic_probability=0.84, weights_json="{}",
                                 generated_at=datetime(2026, 8, 27, 13, tzinfo=timezone.utc)))
    session.flush()
    return m


def test_computes_market_model_spread(quant_db):
    with quant_db() as s:
        _setup(s, comparison_price=0.72)
        rows = join_forecasts_with_market(s, race_id="nc-sen-2026", run_key=RK)
        s.commit()
        by_series = {r.series: r for r in rows}
        assert set(by_series) == {"quant", "ensemble"}
        # market YES = DEM, contract YES = DEM -> no flip; spread = 0.72 - fair_value
        assert by_series["quant"].market_probability == pytest.approx(0.72)
        assert by_series["quant"].market_model_spread == pytest.approx(0.72 - 0.80)
        assert by_series["quant"].abs_spread == pytest.approx(0.08)
        assert by_series["ensemble"].market_model_spread == pytest.approx(0.72 - 0.83)
        assert by_series["ensemble"].robustness in {"HIGH", "MEDIUM", "LOW"}
        assert by_series["ensemble"].liquidity == 50000.0


def test_orients_market_when_yes_side_is_the_other_party(quant_db):
    with quant_db() as s:
        _setup(s, market_yes_party="REP", comparison_price=0.30)  # market YES = REP -> P(Dem) = 0.70
        rows = join_forecasts_with_market(s, race_id="nc-sen-2026", run_key=RK)
        s.commit()
        assert next(r for r in rows if r.series == "quant").market_probability == pytest.approx(0.70)
        assert next(r for r in rows if r.series == "quant").market_model_spread == pytest.approx(0.70 - 0.80)


def test_noop_without_yes_party_or_snapshot(quant_db):
    with quant_db() as s:
        m = Market(platform_market_id="9", question="ambiguous")
        s.add(m)
        s.flush()
        s.add(Race(race_id="x-sen-2026", state="XX", office="senate", cycle=2026,
                   election_date=datetime(2026, 11, 3).date(), polymarket_market_id=m.id,
                   market_yes_party=None))  # no orientation
        s.flush()
        assert join_forecasts_with_market(s, race_id="x-sen-2026", run_key=RK) == []

    with quant_db() as s:
        s.add(Race(race_id="y-sen-2026", state="YY", office="senate", cycle=2026,
                   election_date=datetime(2026, 11, 3).date()))  # no linked market
        s.flush()
        assert join_forecasts_with_market(s, race_id="y-sen-2026", run_key=RK) == []


def test_idempotent(quant_db):
    with quant_db() as s:
        _setup(s)
        first = join_forecasts_with_market(s, race_id="nc-sen-2026", run_key=RK)
        s.commit()
        again = join_forecasts_with_market(s, race_id="nc-sen-2026", run_key=RK)
        s.commit()
        assert {r.id for r in first} == {r.id for r in again}
        assert s.query(ForecastMarketComparison).count() == len(first)
