"""Pure scoring + calibration math (spec sections 34, 35).

Everything here is a pure function on ``(probability, outcome)`` pairs, where ``probability`` is
P(YES) in [0, 1] and ``outcome`` is 0.0 or 1.0. N is always carried through -- no aggregate is
returned without its sample size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

STANDARD_HORIZONS: tuple[int, ...] = (90, 60, 30, 14, 7, 1)
LOW_CONFIDENCE_N = 20  # below this, aggregates are flagged low_confidence

_EPS = 1e-15


def _check(probability: float, outcome: float) -> tuple[float, float]:
    p = float(probability)
    y = float(outcome)
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"probability must be in [0, 1], got {p}")
    if y not in (0.0, 1.0):
        raise ValueError(f"outcome must be 0 or 1, got {y}")
    return p, y


def brier(probability: float, outcome: float) -> float:
    """``(p - y)^2`` -- lower is better, 0 is perfect, 0.25 is a coin flip."""
    p, y = _check(probability, outcome)
    return (p - y) ** 2


def log_loss(probability: float, outcome: float, *, eps: float = _EPS) -> float:
    """Negative log-likelihood, with p clamped to ``[eps, 1-eps]`` so 0/1 forecasts stay finite."""
    p, y = _check(probability, outcome)
    p = min(max(p, eps), 1.0 - eps)
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def party_direction_error(probability: float, outcome: float) -> float:
    """1.0 if the forecast landed on the wrong side of 0.5; 0.0 otherwise; 0.5 for an exact 0.5."""
    p, y = _check(probability, outcome)
    if p == 0.5:
        return 0.5
    predicted_yes = p > 0.5
    actual_yes = y == 1.0
    return 0.0 if predicted_yes == actual_yes else 1.0


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    n: int
    mean_predicted: float
    observed_rate: float


@dataclass(frozen=True)
class Aggregate:
    n: int
    mean_brier: float | None
    mean_log_loss: float | None
    mean_predicted: float | None
    resolution_rate: float | None  # observed share of YES outcomes
    direction_error_rate: float | None
    calibration_error: float | None  # mean |mean_predicted - observed_rate| across populated bins
    low_confidence: bool
    bins: tuple[CalibrationBin, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "mean_brier": self.mean_brier,
            "mean_log_loss": self.mean_log_loss,
            "mean_predicted": self.mean_predicted,
            "resolution_rate": self.resolution_rate,
            "direction_error_rate": self.direction_error_rate,
            "calibration_error": self.calibration_error,
            "low_confidence": self.low_confidence,
            "bins": [
                {
                    "range": [b.lower, b.upper],
                    "n": b.n,
                    "mean_predicted": b.mean_predicted,
                    "observed_rate": b.observed_rate,
                }
                for b in self.bins
            ],
        }


def calibration_bins(
    pairs: Sequence[tuple[float, float]], *, n_bins: int = 10
) -> list[CalibrationBin]:
    """Bin ``(p, y)`` pairs by predicted probability; report mean predicted vs observed rate."""
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    edges = [i / n_bins for i in range(n_bins + 1)]
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for p, y in pairs:
        p, y = _check(p, y)
        idx = min(int(p * n_bins), n_bins - 1)
        buckets[idx].append((p, y))
    out: list[CalibrationBin] = []
    for i, b in enumerate(buckets):
        if not b:
            continue
        preds = [p for p, _ in b]
        outs = [y for _, y in b]
        out.append(
            CalibrationBin(
                lower=edges[i],
                upper=edges[i + 1],
                n=len(b),
                mean_predicted=sum(preds) / len(preds),
                observed_rate=sum(outs) / len(outs),
            )
        )
    return out


def aggregate(pairs: Iterable[tuple[float, float]], *, n_bins: int = 10) -> Aggregate:
    """Full metric bundle for a set of ``(probability, outcome)`` pairs."""
    pairs = [_check(p, y) for p, y in pairs]
    n = len(pairs)
    if n == 0:
        return Aggregate(0, None, None, None, None, None, None, True, ())
    briers = [brier(p, y) for p, y in pairs]
    lls = [log_loss(p, y) for p, y in pairs]
    des = [party_direction_error(p, y) for p, y in pairs]
    bins = calibration_bins(pairs, n_bins=n_bins)
    cal_err = (
        sum(abs(b.mean_predicted - b.observed_rate) for b in bins) / len(bins) if bins else None
    )
    return Aggregate(
        n=n,
        mean_brier=sum(briers) / n,
        mean_log_loss=sum(lls) / n,
        mean_predicted=sum(p for p, _ in pairs) / n,
        resolution_rate=sum(y for _, y in pairs) / n,
        direction_error_rate=sum(des) / n,
        calibration_error=cal_err,
        low_confidence=n < LOW_CONFIDENCE_N,
        bins=tuple(bins),
    )
