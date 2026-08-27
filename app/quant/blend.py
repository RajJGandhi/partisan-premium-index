"""Blend the polling margin with the fundamentals margin (spec section 14).

    BaseAlpha  = 1 - e^(-n_eff / 2.5)
    alpha      = min(BaseAlpha, time_cap(days_to_election)) * staleness_multiplier(newest_poll_age)
    mu         = alpha * PollingMargin + (1 - alpha) * FundamentalMargin

Polling earns more weight with more independent polls, newer polls, and less time to election.
With no usable polls, ``alpha = 0`` and the forecast is fundamentals-only. With no fundamentals
(no state lean) but usable polls, ``alpha = 1`` and the forecast is polling-only.
"""

from __future__ import annotations

import math
from typing import Optional

from app.quant.config import QUANT_V1, MethodologyConfig


def base_alpha(n_eff: float, cfg: MethodologyConfig = QUANT_V1) -> float:
    if n_eff <= 0:
        return 0.0
    return 1.0 - math.exp(-float(n_eff) / cfg.alpha_neff_scale)


def time_cap_alpha(days_to_election: float, cfg: MethodologyConfig = QUANT_V1) -> float:
    """Max alpha permitted this far from election day. Caps are step thresholds, ascending in days.

    ``alpha_time_caps`` is ``((180, 0.65), (90, 0.75), (30, 0.88), (0, 0.93))`` -- read as
    ">180 days -> 0.65; 91-180 -> 0.75; 31-90 -> 0.88; 0-30 -> 0.93".
    """
    d = max(0.0, float(days_to_election))
    caps = sorted(cfg.alpha_time_caps, key=lambda x: x[0], reverse=True)  # (180,..),(90,..),..
    for threshold, cap in caps:
        if d > threshold:
            return cap
    # d <= smallest threshold (0.0) -> use the closest-to-election cap
    return caps[-1][1]


def staleness_multiplier(
    newest_poll_age_days: Optional[float], cfg: MethodologyConfig = QUANT_V1
) -> float:
    """Multiplier applied to alpha when the newest usable poll is old.

    ``alpha_staleness_steps`` is ``((30, 1.00), (60, 0.65))`` -> "<=30d: 1.0; 31-60d: 0.65;
    >60d: alpha_staleness_beyond_last_step (0.35)". ``None`` (no polls) -> 1.0 (alpha is already 0).
    """
    if newest_poll_age_days is None:
        return 1.0
    age = max(0.0, float(newest_poll_age_days))
    for threshold, mult in sorted(cfg.alpha_staleness_steps, key=lambda x: x[0]):
        if age <= threshold:
            return mult
    return cfg.alpha_staleness_beyond_last_step


def resolve_poll_weight(
    *,
    n_eff: float,
    days_to_election: float,
    newest_poll_age_days: Optional[float],
    has_usable_polls: bool,
    has_fundamentals: bool,
    cfg: MethodologyConfig = QUANT_V1,
) -> tuple[float, dict]:
    """Return ``(alpha, detail)`` -- alpha in [0, 1]."""
    if not has_usable_polls:
        return 0.0, {"reason": "no usable polls", "alpha": 0.0}
    if not has_fundamentals:
        return 1.0, {"reason": "no fundamentals (state lean unavailable); polling-only", "alpha": 1.0}

    ba = base_alpha(n_eff, cfg)
    cap = time_cap_alpha(days_to_election, cfg)
    stale = staleness_multiplier(newest_poll_age_days, cfg)
    alpha = min(ba, cap) * stale
    alpha = max(0.0, min(1.0, alpha))
    return alpha, {
        "base_alpha": ba,
        "time_cap": cap,
        "staleness_multiplier": stale,
        "alpha": alpha,
        "n_eff": n_eff,
        "days_to_election": days_to_election,
        "newest_poll_age_days": newest_poll_age_days,
    }


def expected_margin(
    alpha: float,
    polling_margin: Optional[float],
    fundamental_margin: Optional[float],
) -> Optional[float]:
    """mu = alpha*polling + (1-alpha)*fundamentals, with sensible behaviour when one side is absent."""
    if polling_margin is None and fundamental_margin is None:
        return None
    if polling_margin is None:
        return float(fundamental_margin)  # type: ignore[arg-type]
    if fundamental_margin is None:
        return float(polling_margin)
    a = max(0.0, min(1.0, float(alpha)))
    return a * float(polling_margin) + (1.0 - a) * float(fundamental_margin)
