from __future__ import annotations

import pytest

from app.quant.config import QUANT_V1
from app.quant.fundamentals import compute_fundamentals, incumbency_adjustment


def test_incumbency_signs_and_open_seat():
    assert incumbency_adjustment("senate", "DEM", QUANT_V1) == 1.5
    assert incumbency_adjustment("senate", "REP", QUANT_V1) == -1.5
    assert incumbency_adjustment("senate", None, QUANT_V1) == 0.0
    assert incumbency_adjustment("governor", "DEM", QUANT_V1) == 2.0
    assert incumbency_adjustment("governor", "REP", QUANT_V1) == -2.0


def test_senate_formula_is_lean_plus_environment_plus_incumbency():
    f = compute_fundamentals(
        office="senate", state_lean=3.0, national_environment=4.0, incumbent_party="DEM", cfg=QUANT_V1
    )
    assert f.fundamental_margin == pytest.approx(3.0 + 4.0 + 1.5)


def test_governor_formula_discounts_national_environment_by_0_65():
    f = compute_fundamentals(
        office="governor", state_lean=-8.0, national_environment=6.0, incumbent_party="REP", cfg=QUANT_V1
    )
    assert f.fundamental_margin == pytest.approx(-8.0 + 0.65 * 6.0 - 2.0)


def test_missing_national_environment_is_not_treated_as_zero_silently():
    f = compute_fundamentals(
        office="senate", state_lean=2.0, national_environment=None, incumbent_party=None, cfg=QUANT_V1
    )
    assert f.fundamental_margin == pytest.approx(2.0)  # lean + 0 + 0
    assert f.detail["national_environment_missing"] is True


def test_missing_state_lean_yields_no_fundamental_margin():
    f = compute_fundamentals(
        office="senate", state_lean=None, national_environment=4.0, incumbent_party="DEM", cfg=QUANT_V1
    )
    assert f.fundamental_margin is None
    assert "reason" in f.detail
