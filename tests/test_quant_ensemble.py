from __future__ import annotations

import statistics

import pytest

from app.quant.config import QUANT_V1
from app.quant.ensemble import (
    combine_ensemble,
    max_pairwise_disagreement,
    model_dispersion,
    robustness_band,
)


def test_predeclared_weights_are_applied():
    r = combine_ensemble(quant=0.90, openai=0.80, anthropic=0.70)
    assert r.available
    assert r.ensemble_probability == pytest.approx(0.60 * 0.90 + 0.20 * 0.80 + 0.20 * 0.70)
    assert r.weights == {"quant": 0.60, "openai": 0.20, "anthropic": 0.20}


def test_missing_component_makes_ensemble_unavailable_without_reweighting():
    r = combine_ensemble(quant=0.90, openai=None, anthropic=0.70)
    assert not r.available
    assert r.ensemble_probability is None
    assert "not reweighted" in r.unavailable_reason.lower() or "not reweighted" in r.unavailable_reason
    # the components that ARE present are still reported, just not blended
    assert r.components["quant"] == 0.90
    assert r.components["anthropic"] == 0.70


def test_all_missing_is_unavailable():
    assert not combine_ensemble(quant=None, openai=None, anthropic=None).available


def test_dispersion_is_population_stdev():
    vals = [0.9, 0.8, 0.7]
    assert model_dispersion(vals) == pytest.approx(statistics.pstdev(vals))
    assert model_dispersion([0.5]) == 0.0


def test_max_pairwise_disagreement():
    assert max_pairwise_disagreement([0.45, 0.72, 0.61]) == pytest.approx(0.27)
    assert max_pairwise_disagreement([0.5]) == 0.0


def test_robustness_high_when_models_agree_and_diverge_from_market():
    # models tightly clustered ~0.90, market 0.75 -> gap 15pt, pairwise ~2pt
    r = combine_ensemble(quant=0.90, openai=0.89, anthropic=0.91, market_probability=0.75)
    assert r.robustness == "HIGH"


def test_robustness_low_when_models_themselves_disagree():
    # the "premium" is really model disagreement: quant 45, gpt 72, claude 61
    r = combine_ensemble(quant=0.45, openai=0.72, anthropic=0.61, market_probability=0.60)
    assert r.robustness == "LOW"


def test_robustness_band_direct():
    cfg = QUANT_V1
    assert robustness_band(market_probability=None, ensemble_probability=0.9, max_pairwise=0.02, cfg=cfg) is None
    assert robustness_band(market_probability=0.75, ensemble_probability=0.90, max_pairwise=0.05, cfg=cfg) == "HIGH"
    assert robustness_band(market_probability=0.80, ensemble_probability=0.88, max_pairwise=0.12, cfg=cfg) == "MEDIUM"
    assert robustness_band(market_probability=0.60, ensemble_probability=0.55, max_pairwise=0.30, cfg=cfg) == "LOW"


def test_weights_must_sum_to_one_or_it_raises():
    from dataclasses import replace

    bad = replace(QUANT_V1, ensemble_weights={"quant": 0.5, "openai": 0.2, "anthropic": 0.2})
    with pytest.raises(ValueError):
        combine_ensemble(quant=0.9, openai=0.8, anthropic=0.7, cfg=bad)
