from __future__ import annotations

import math

import pytest

from app.eval.metrics import (
    LOW_CONFIDENCE_N,
    STANDARD_HORIZONS,
    aggregate,
    brier,
    calibration_bins,
    log_loss,
    party_direction_error,
)


def test_standard_horizons():
    assert STANDARD_HORIZONS == (90, 60, 30, 14, 7, 1)


def test_brier():
    assert brier(1.0, 1.0) == 0.0
    assert brier(0.0, 1.0) == 1.0
    assert brier(0.5, 1.0) == 0.25
    assert brier(0.7, 0.0) == pytest.approx(0.49)
    with pytest.raises(ValueError):
        brier(1.2, 1.0)
    with pytest.raises(ValueError):
        brier(0.5, 0.5)


def test_log_loss_clamps_at_extremes():
    assert log_loss(0.9, 1.0) == pytest.approx(-math.log(0.9))
    # a confident-wrong 0/1 forecast stays finite
    assert math.isfinite(log_loss(1.0, 0.0))
    assert math.isfinite(log_loss(0.0, 1.0))


def test_party_direction_error():
    assert party_direction_error(0.7, 1.0) == 0.0
    assert party_direction_error(0.7, 0.0) == 1.0
    assert party_direction_error(0.3, 0.0) == 0.0
    assert party_direction_error(0.5, 1.0) == 0.5


def test_calibration_bins_only_populated():
    pairs = [(0.05, 0.0), (0.15, 0.0), (0.92, 1.0), (0.95, 1.0), (0.9, 0.0)]
    bins = calibration_bins(pairs, n_bins=10)
    assert all(b.n > 0 for b in bins)  # empty bins are skipped
    assert {(round(b.lower, 1), b.n) for b in bins} == {(0.0, 1), (0.1, 1), (0.9, 3)}
    top = next(b for b in bins if b.lower == pytest.approx(0.9))
    assert top.observed_rate == pytest.approx(2 / 3)  # 0.92, 0.95 -> 1; 0.9 -> 0


def test_aggregate_always_reports_n_and_low_confidence():
    empty = aggregate([])
    assert empty.n == 0 and empty.low_confidence is True and empty.mean_brier is None

    small = aggregate([(0.8, 1.0), (0.2, 0.0), (0.6, 1.0)])
    assert small.n == 3 and small.low_confidence is True
    assert small.mean_brier == pytest.approx((0.04 + 0.04 + 0.16) / 3)
    assert small.resolution_rate == pytest.approx(2 / 3)
    assert small.direction_error_rate == 0.0

    big = aggregate([(0.8, 1.0)] * (LOW_CONFIDENCE_N + 1))
    assert big.low_confidence is False
