"""Uncertainty model (spec section 15).

The election margin is modelled as ``Normal(mu, sigma_total)`` where ``mu`` is the expected margin
and

    sigma_total = sqrt(sigma_time^2 + sigma_polling^2 + sigma_office^2 + sigma_status^2)

Each component is stored separately. ``sigma_time`` is interpolated continuously from a schedule
of (days_before_election, sigma) points; the others are step functions. This is what stops the
model turning a poll lead directly into certainty.
"""

from __future__ import annotations

import math

from app.quant.config import QUANT_V1, MethodologyConfig
from app.quant.types import UncertaintyBreakdown


def time_sigma(days_to_election: float, cfg: MethodologyConfig = QUANT_V1) -> float:
    """Linearly interpolate the time-uncertainty schedule; clamp outside its range."""
    schedule = sorted(cfg.sigma_time_schedule, key=lambda x: x[0])  # ascending days
    d = float(days_to_election)
    min_days, min_sigma = schedule[0]
    max_days, max_sigma = schedule[-1]
    if d <= min_days:
        return min_sigma
    if d >= max_days:
        return max_sigma
    for (d0, s0), (d1, s1) in zip(schedule, schedule[1:], strict=False):
        if d0 <= d <= d1:
            frac = (d - d0) / (d1 - d0)
            return s0 + frac * (s1 - s0)
    return max_sigma  # unreachable, keeps type checkers happy


def polling_sigma(n_eff: float, cfg: MethodologyConfig = QUANT_V1) -> float:
    """Additive sparse-polling term. ``sigma_polling_steps`` is ``(n_eff_threshold, sigma)`` desc."""
    steps = sorted(cfg.sigma_polling_steps, key=lambda x: x[0], reverse=True)
    n = max(0.0, float(n_eff))
    for threshold, sigma in steps:
        if n >= threshold:
            return sigma
    return steps[-1][1]


def office_sigma(office: str, cfg: MethodologyConfig = QUANT_V1) -> float:
    return cfg.office_sigma(office)


def status_sigma(
    *,
    nominees_confirmed: bool,
    candidate_mapping_confidence: float,
    cfg: MethodologyConfig = QUANT_V1,
) -> float:
    """Extra uncertainty from unresolved nominee status / imperfect candidate mapping.

    Below ``abstain_mapping_confidence_below`` the engine abstains instead of adding sigma, so this
    function only handles the "uncertain but usable" band.
    """
    sigma = 0.0
    if not nominees_confirmed:
        sigma = math.hypot(sigma, cfg.sigma_status_unconfirmed_nominee)
    if candidate_mapping_confidence < 1.0 and candidate_mapping_confidence >= cfg.abstain_mapping_confidence_below:
        # scale the low-mapping term by how far below 1.0 the confidence is
        shortfall = (1.0 - candidate_mapping_confidence) / (1.0 - cfg.abstain_mapping_confidence_below)
        sigma = math.hypot(sigma, cfg.sigma_status_low_mapping_confidence * shortfall)
    return sigma


def total_sigma(
    *,
    days_to_election: float,
    n_eff: float,
    office: str,
    nominees_confirmed: bool,
    candidate_mapping_confidence: float,
    cfg: MethodologyConfig = QUANT_V1,
) -> UncertaintyBreakdown:
    s_time = time_sigma(days_to_election, cfg)
    s_poll = polling_sigma(n_eff, cfg)
    s_office = office_sigma(office, cfg)
    s_status = status_sigma(
        nominees_confirmed=nominees_confirmed,
        candidate_mapping_confidence=candidate_mapping_confidence,
        cfg=cfg,
    )
    s_total = math.sqrt(s_time ** 2 + s_poll ** 2 + s_office ** 2 + s_status ** 2)
    return UncertaintyBreakdown(
        sigma_total=s_total,
        sigma_time=s_time,
        sigma_polling=s_poll,
        sigma_office=s_office,
        sigma_status=s_status,
    )
