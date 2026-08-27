from __future__ import annotations

import math

import pytest

from app.quant.blend import (
    base_alpha,
    expected_margin,
    resolve_poll_weight,
    staleness_multiplier,
    time_cap_alpha,
)
from app.quant.config import QUANT_V1


def test_base_alpha_formula():
    assert base_alpha(0, QUANT_V1) == 0.0
    assert base_alpha(2.5, QUANT_V1) == pytest.approx(1 - math.exp(-1))
    assert base_alpha(100, QUANT_V1) == pytest.approx(1.0, abs=1e-6)
    # monotonic increasing in n_eff
    assert base_alpha(1, QUANT_V1) < base_alpha(5, QUANT_V1) < base_alpha(10, QUANT_V1)


def test_time_cap_steps():
    assert time_cap_alpha(365, QUANT_V1) == 0.65  # > 180
    assert time_cap_alpha(120, QUANT_V1) == 0.75  # 91-180
    assert time_cap_alpha(60, QUANT_V1) == 0.88   # 31-90
    assert time_cap_alpha(10, QUANT_V1) == 0.93   # 0-30
    assert time_cap_alpha(0, QUANT_V1) == 0.93


def test_staleness_multiplier_steps():
    assert staleness_multiplier(10, QUANT_V1) == 1.0
    assert staleness_multiplier(30, QUANT_V1) == 1.0
    assert staleness_multiplier(45, QUANT_V1) == 0.65
    assert staleness_multiplier(60, QUANT_V1) == 0.65
    assert staleness_multiplier(90, QUANT_V1) == 0.35
    assert staleness_multiplier(None, QUANT_V1) == 1.0


def test_no_usable_polls_forces_alpha_zero():
    alpha, detail = resolve_poll_weight(
        n_eff=0.0, days_to_election=60, newest_poll_age_days=None,
        has_usable_polls=False, has_fundamentals=True, cfg=QUANT_V1,
    )
    assert alpha == 0.0
    assert "no usable polls" in detail["reason"]


def test_no_fundamentals_forces_alpha_one():
    alpha, detail = resolve_poll_weight(
        n_eff=5.0, days_to_election=60, newest_poll_age_days=5,
        has_usable_polls=True, has_fundamentals=False, cfg=QUANT_V1,
    )
    assert alpha == 1.0
    assert "polling-only" in detail["reason"]


def test_alpha_is_capped_by_time_and_staleness():
    # lots of polls (base_alpha ~1) but 200 days out -> cap 0.65, fresh polls -> x1.0
    alpha, _ = resolve_poll_weight(
        n_eff=20.0, days_to_election=200, newest_poll_age_days=5,
        has_usable_polls=True, has_fundamentals=True, cfg=QUANT_V1,
    )
    assert alpha == pytest.approx(0.65, abs=1e-6)
    # same, but newest poll is 70 days old -> x0.35
    alpha2, _ = resolve_poll_weight(
        n_eff=20.0, days_to_election=200, newest_poll_age_days=70,
        has_usable_polls=True, has_fundamentals=True, cfg=QUANT_V1,
    )
    assert alpha2 == pytest.approx(0.65 * 0.35, abs=1e-6)


def test_expected_margin_blends():
    assert expected_margin(0.75, 8.0, 2.0) == pytest.approx(0.75 * 8 + 0.25 * 2)
    assert expected_margin(0.0, 8.0, 2.0) == pytest.approx(2.0)
    assert expected_margin(1.0, 8.0, 2.0) == pytest.approx(8.0)
    assert expected_margin(0.5, None, 3.0) == 3.0
    assert expected_margin(0.5, 5.0, None) == 5.0
    assert expected_margin(0.5, None, None) is None
