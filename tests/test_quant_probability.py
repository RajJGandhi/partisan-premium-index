from __future__ import annotations

import pytest

from app.quant.config import QUANT_V1
from app.quant.probability import margin_to_win_probability, standard_normal_cdf


def test_standard_normal_cdf_known_values():
    assert standard_normal_cdf(0.0) == pytest.approx(0.5)
    assert standard_normal_cdf(1.0) == pytest.approx(0.8413447, abs=1e-6)
    assert standard_normal_cdf(-1.0) == pytest.approx(0.1586553, abs=1e-6)
    assert standard_normal_cdf(1.959964) == pytest.approx(0.975, abs=1e-5)


def test_cdf_symmetry():
    for z in (0.3, 1.1, 2.4, 3.7):
        assert standard_normal_cdf(z) + standard_normal_cdf(-z) == pytest.approx(1.0)


def test_margin_to_probability_basic():
    out = margin_to_win_probability(0.0, 5.0, QUANT_V1)
    assert out["p_dem_win"] == pytest.approx(0.5)
    assert out["p_rep_win"] == pytest.approx(0.5)
    out2 = margin_to_win_probability(5.0, 5.0, QUANT_V1)
    assert out2["z"] == pytest.approx(1.0)
    assert out2["p_dem_win"] == pytest.approx(0.8413447, abs=1e-6)


def test_probability_is_capped_but_uncapped_is_retained():
    out = margin_to_win_probability(40.0, 3.0, QUANT_V1)  # z ~ 13 -> ~1.0
    assert out["p_dem_win"] == QUANT_V1.probability_ceiling
    assert out["p_dem_win_uncapped"] > 0.999999
    assert out["p_dem_win_uncapped"] <= 1.0
    out_low = margin_to_win_probability(-40.0, 3.0, QUANT_V1)
    assert out_low["p_dem_win"] == QUANT_V1.probability_floor
    assert out_low["p_dem_win_uncapped"] < 1e-6


def test_none_margin_propagates_none():
    out = margin_to_win_probability(None, 5.0, QUANT_V1)
    assert out["p_dem_win"] is None and out["z"] is None


def test_zero_sigma_raises():
    with pytest.raises(ValueError):
        margin_to_win_probability(1.0, 0.0, QUANT_V1)
