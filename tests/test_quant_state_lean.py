from __future__ import annotations

import pytest

from app.quant.config import QUANT_V1
from app.quant.state_lean import compute_state_lean
from app.quant.types import PresidentialResult, StateHistory


def _history(state_margins: dict[int, float], national_margins: dict[int, float]) -> StateHistory:
    return StateHistory(
        state="ST",
        state_results={y: PresidentialResult(y, dem_margin_pct=m) for y, m in state_margins.items()},
        national_results={y: PresidentialResult(y, dem_margin_pct=m) for y, m in national_margins.items()},
    )


def test_lean_is_state_minus_national_weighted():
    # every year: state D+3, national D+1  -> lean +2 every year -> weighted lean +2
    hist = _history({2016: 3, 2020: 3, 2024: 3}, {2016: 1, 2020: 1, 2024: 1})
    lean, detail = compute_state_lean(hist, QUANT_V1)
    assert lean == pytest.approx(2.0)
    assert detail["years"]["2024"]["lean"] == pytest.approx(2.0)


def test_weighting_favours_2024():
    # lean 2016=+10, 2020=0, 2024=-10 ; weights .15/.30/.55
    hist = _history({2016: 10, 2020: 0, 2024: -10}, {2016: 0, 2020: 0, 2024: 0})
    lean, _ = compute_state_lean(hist, QUANT_V1)
    assert lean == pytest.approx(0.15 * 10 + 0.30 * 0 + 0.55 * -10)
    assert lean < 0  # 2024 dominates


def test_missing_year_weight_is_redistributed():
    # only 2020 and 2024 present -> weights renormalised to .30/.55 -> .3529 / .6471
    hist = _history({2020: 4, 2024: -4}, {2020: 0, 2024: 0})
    lean, detail = compute_state_lean(hist, QUANT_V1)
    total = 0.30 + 0.55
    assert lean == pytest.approx((0.30 / total) * 4 + (0.55 / total) * -4)
    assert detail["redistributed"] is True
    assert 2016 in detail["missing_years"]


def test_r_plus_five_is_negative_five():
    # state R+5 (=-5), national tied -> lean -5
    hist = _history({2016: -5, 2020: -5, 2024: -5}, {2016: 0, 2020: 0, 2024: 0})
    lean, _ = compute_state_lean(hist, QUANT_V1)
    assert lean == pytest.approx(-5.0)


def test_none_history_returns_none():
    lean, detail = compute_state_lean(None, QUANT_V1)
    assert lean is None
    assert "reason" in detail


def test_no_overlapping_years_returns_none():
    hist = _history({2012: 5}, {2016: 1})
    lean, detail = compute_state_lean(hist, QUANT_V1)
    assert lean is None
    assert detail["missing_years"]


def test_presidential_result_from_votes():
    r = PresidentialResult(2024, dem_votes=1_000_000, rep_votes=1_100_000)
    assert r.margin == pytest.approx(100.0 * (-100_000) / 2_100_000)
