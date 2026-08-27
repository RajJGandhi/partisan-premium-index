"""Lead/lag analysis between two twice-daily forecast series (spec section 36).

Given the run-slot time series for two series (e.g. ``market`` and ``quant``), compare their
first differences at small integer lags and classify:

- **A_leads**  -- series A's move at t best predicts series B's move at t+k, k > 0
- **B_leads**  -- the reverse
- **synchronous** -- the strongest correlation is at lag 0
- **insufficient_data** -- fewer than ``min_points`` overlapping observations

This is exploratory (helps separate "market irrationality" from "market incorporated information
earlier"); it makes no significance claim on a short series.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from app.eval.series import Observation


@dataclass(frozen=True)
class LeadLagResult:
    series_a: str
    series_b: str
    classification: str  # A_leads / B_leads / synchronous / insufficient_data
    best_lag: Optional[int]
    correlations: dict[int, float]
    n_overlapping: int
    note: str = ""


def _resample_daily(observations: Sequence[Observation]) -> dict:
    """Latest probability per calendar day (keeps the twice-daily series comparable by date)."""
    by_day: dict = {}
    for o in sorted(observations, key=lambda x: x.observed_at):
        by_day[o.observed_at.date()] = o.probability
    return by_day


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx**0.5 * vy**0.5)


def leadlag_analysis(
    series_a_obs: Sequence[Observation],
    series_b_obs: Sequence[Observation],
    *,
    series_a: str = "A",
    series_b: str = "B",
    max_lag: int = 3,
    min_points: int = 6,
) -> LeadLagResult:
    a_day = _resample_daily(series_a_obs)
    b_day = _resample_daily(series_b_obs)
    common_days = sorted(set(a_day) & set(b_day))
    if len(common_days) < min_points + 1:
        return LeadLagResult(
            series_a, series_b, "insufficient_data", None, {}, len(common_days),
            note=f"need >= {min_points + 1} overlapping days, have {len(common_days)}",
        )

    a = [a_day[d] for d in common_days]
    b = [b_day[d] for d in common_days]
    da = [a[i] - a[i - 1] for i in range(1, len(a))]
    db = [b[i] - b[i - 1] for i in range(1, len(b))]

    corrs: dict[int, float] = {}
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x, y = da[: len(da) - lag], db[lag:]
        else:
            x, y = da[-lag:], db[: len(db) + lag]
        if len(x) >= 2:
            c = _pearson(x, y)
            if c is not None:
                corrs[lag] = c
    if not corrs:
        return LeadLagResult(series_a, series_b, "insufficient_data", None, {}, len(common_days))

    # strongest |correlation|; among lags within a small tolerance of the best, prefer the one
    # closest to 0 (call it synchronous rather than inventing a lead from a numerical tie)
    peak = max(abs(c) for c in corrs.values())
    best_lag = min((k for k, c in corrs.items() if abs(c) >= peak - 1e-9), key=abs)
    if best_lag > 0:
        cls = "A_leads"
    elif best_lag < 0:
        cls = "B_leads"
    else:
        cls = "synchronous"
    return LeadLagResult(series_a, series_b, cls, best_lag, corrs, len(common_days))
