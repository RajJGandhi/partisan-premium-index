from __future__ import annotations

import math

import pytest

from app.quant.config import QUANT_V1
from app.quant.uncertainty import (
    office_sigma,
    polling_sigma,
    status_sigma,
    time_sigma,
    total_sigma,
)


def test_time_sigma_matches_schedule_points_exactly():
    for days, sigma in QUANT_V1.sigma_time_schedule:
        assert time_sigma(days, QUANT_V1) == pytest.approx(sigma)


def test_time_sigma_interpolates_between_points():
    # midpoint of (60 -> 5.5) and (90 -> 6.3) at 75 days -> 5.9
    assert time_sigma(75, QUANT_V1) == pytest.approx(5.9)


def test_time_sigma_clamps_outside_range():
    assert time_sigma(1000, QUANT_V1) == 9.0
    assert time_sigma(0, QUANT_V1) == 3.3
    assert time_sigma(-10, QUANT_V1) == 3.3


def test_polling_sigma_steps():
    assert polling_sigma(10, QUANT_V1) == 0.0
    assert polling_sigma(7, QUANT_V1) == 0.0
    assert polling_sigma(5, QUANT_V1) == 0.5
    assert polling_sigma(3, QUANT_V1) == 1.25
    assert polling_sigma(1, QUANT_V1) == 2.5
    assert polling_sigma(0, QUANT_V1) == 4.0


def test_office_sigma():
    assert office_sigma("senate", QUANT_V1) == 0.0
    assert office_sigma("governor", QUANT_V1) == 0.75


def test_status_sigma_adds_only_when_uncertain():
    assert status_sigma(nominees_confirmed=True, candidate_mapping_confidence=1.0, cfg=QUANT_V1) == 0.0
    s = status_sigma(nominees_confirmed=False, candidate_mapping_confidence=1.0, cfg=QUANT_V1)
    assert s == pytest.approx(QUANT_V1.sigma_status_unconfirmed_nominee)


def test_total_sigma_is_root_sum_of_squares():
    b = total_sigma(
        days_to_election=90, n_eff=1.0, office="governor",
        nominees_confirmed=True, candidate_mapping_confidence=1.0, cfg=QUANT_V1,
    )
    expected = math.sqrt(b.sigma_time ** 2 + b.sigma_polling ** 2 + b.sigma_office ** 2 + b.sigma_status ** 2)
    assert b.sigma_total == pytest.approx(expected)
    assert b.sigma_time == pytest.approx(6.3)
    assert b.sigma_polling == 2.5
    assert b.sigma_office == 0.75


def test_total_sigma_never_zero():
    b = total_sigma(
        days_to_election=1, n_eff=50, office="senate",
        nominees_confirmed=True, candidate_mapping_confidence=1.0, cfg=QUANT_V1,
    )
    assert b.sigma_total >= 3.3  # sigma_time floor alone
