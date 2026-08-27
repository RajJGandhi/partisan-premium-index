"""Quantitative polling average (spec sections 11-12).

All work happens in expected vote-margin space (Democratic minus Republican points), never in
probability space. The weighted average is

    PollingMargin = sum_i W_i * M_i / sum_i W_i

with per-poll weight

    W_i = Recency_i * Sample_i * Population_i * Quality_i * Sponsor_i * Flooding_i

and effective poll count

    n_eff = (sum_i W_i)^2 / sum_i W_i^2
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Sequence

from app.quant.config import QUANT_V1, MethodologyConfig
from app.quant.types import GenericBallotPoll, NormalizedPoll, PollingAverage


def recency_weight(age_days: float, half_life_days: float) -> float:
    """``0.5 ** (age_days / half_life)`` -- exponential decay, 1.0 at age 0."""
    age = max(0.0, float(age_days))
    return 0.5 ** (age / float(half_life_days))


def sample_weight(sample_size: int | None, cfg: MethodologyConfig = QUANT_V1) -> float:
    """``sqrt(N / reference_n)`` with N floored and capped so no single poll dominates."""
    ref = cfg.sample_size_reference_n
    if sample_size is None or sample_size <= 0:
        n = cfg.sample_size_reference_n  # unknown sample -> neutral weight of 1.0
    else:
        n = float(sample_size)
    n = min(max(n, cfg.sample_weight_floor_n), cfg.sample_weight_cap_n)
    return math.sqrt(n / ref)


def population_weight(population: str | None, cfg: MethodologyConfig = QUANT_V1) -> float:
    return cfg.population_weight(population)


def quality_weight(grade: str | None, cfg: MethodologyConfig = QUANT_V1) -> float:
    return cfg.grade_weight(grade)


def sponsor_weight(
    *, partisan_sponsor: str | None, internal: bool, cfg: MethodologyConfig = QUANT_V1
) -> float:
    return cfg.sponsor_weight(partisan_sponsor=partisan_sponsor, internal=internal)


def _flooding_weights(
    keys: Sequence[tuple[str, date]], cfg: MethodologyConfig
) -> list[float]:
    """Diminishing weight for a pollster that floods the window with near-duplicate releases.

    ``keys`` is ``(normalized_pollster, end_date)`` per poll, in the engine's poll order. Within
    ``pollster_flooding_window_days`` of the most recent poll overall, a given pollster's k-th
    most recent poll (k = 0 newest) is multiplied by ``decay ** k``. Older-than-window polls and
    a pollster's single poll are unaffected (multiplier 1.0). Never deletes a poll.
    """
    if not keys:
        return []
    newest = max(d for _, d in keys)
    window = cfg.pollster_flooding_window_days
    # rank each pollster's in-window polls by recency
    by_pollster: dict[str, list[int]] = defaultdict(list)
    for idx, (pollster, d) in enumerate(keys):
        if (newest - d).days <= window:
            by_pollster[pollster].append(idx)
    multiplier = [1.0] * len(keys)
    for _pollster, idxs in by_pollster.items():
        if len(idxs) <= 1:
            continue
        idxs_sorted = sorted(idxs, key=lambda i: keys[i][1], reverse=True)
        for rank, i in enumerate(idxs_sorted):
            multiplier[i] = cfg.pollster_flooding_decay ** rank
    return multiplier


def _normalize_pollster(name: str) -> str:
    return " ".join(name.strip().lower().split())


def weighted_polling_average(
    polls: Sequence[NormalizedPoll],
    as_of: date,
    cfg: MethodologyConfig = QUANT_V1,
) -> PollingAverage:
    """Compute the PPI weighted polling margin and its diagnostics for a race."""
    usable = [p for p in polls if p.end_date <= as_of]
    if not usable:
        return PollingAverage(
            polling_margin=None,
            n_eff=0.0,
            raw_poll_count=len(polls),
            used_poll_count=0,
            latest_poll_date=None,
            average_poll_age_days=None,
            pollster_diversity=0,
            sum_weights=0.0,
            per_poll=(),
        )

    keys = [(_normalize_pollster(p.pollster), p.end_date) for p in usable]
    flooding = _flooding_weights(keys, cfg)

    per_poll: list[dict] = []
    weights: list[float] = []
    margins: list[float] = []
    for p, flood in zip(usable, flooding, strict=True):
        age = p.age_days(as_of)
        w_rec = recency_weight(age, cfg.poll_half_life_days)
        w_smp = sample_weight(p.sample_size, cfg)
        w_pop = population_weight(p.population, cfg)
        w_qual = quality_weight(p.pollster_grade, cfg)
        w_spon = sponsor_weight(partisan_sponsor=p.partisan_sponsor, internal=p.internal, cfg=cfg)
        w = w_rec * w_smp * w_pop * w_qual * w_spon * flood
        weights.append(w)
        margins.append(p.margin)
        per_poll.append(
            {
                "poll_id": p.poll_id,
                "pollster": p.pollster,
                "end_date": p.end_date.isoformat(),
                "age_days": age,
                "margin": p.margin,
                "weight": w,
                "weight_breakdown": {
                    "recency": w_rec,
                    "sample": w_smp,
                    "population": w_pop,
                    "quality": w_qual,
                    "sponsor": w_spon,
                    "flooding": flood,
                },
            }
        )

    sum_w = math.fsum(weights)
    if sum_w <= 0:
        return PollingAverage(
            polling_margin=None,
            n_eff=0.0,
            raw_poll_count=len(polls),
            used_poll_count=len(usable),
            latest_poll_date=max(p.end_date for p in usable),
            average_poll_age_days=None,
            pollster_diversity=len({k for k, _ in keys}),
            sum_weights=0.0,
            per_poll=tuple(per_poll),
        )

    polling_margin = math.fsum(w * m for w, m in zip(weights, margins, strict=True)) / sum_w
    n_eff = (sum_w ** 2) / math.fsum(w ** 2 for w in weights)
    avg_age = math.fsum(pp["age_days"] * w for pp, w in zip(per_poll, weights, strict=True)) / sum_w

    return PollingAverage(
        polling_margin=polling_margin,
        n_eff=n_eff,
        raw_poll_count=len(polls),
        used_poll_count=len(usable),
        latest_poll_date=max(p.end_date for p in usable),
        average_poll_age_days=avg_age,
        pollster_diversity=len({k for k, _ in keys}),
        sum_weights=sum_w,
        per_poll=tuple(per_poll),
    )


def weighted_generic_ballot_average(
    polls: Sequence[GenericBallotPoll],
    as_of: date,
    cfg: MethodologyConfig = QUANT_V1,
) -> PollingAverage:
    """Same weighting framework applied to national generic-ballot polls (spec section 10)."""
    shim = [
        NormalizedPoll(
            pollster=g.pollster,
            end_date=g.end_date,
            dem_pct=g.dem_pct,
            rep_pct=g.rep_pct,
            sample_size=g.sample_size,
            population=g.population,
            pollster_grade=g.pollster_grade,
            partisan_sponsor=g.partisan_sponsor,
            internal=g.internal,
            source=g.source,
        )
        for g in polls
    ]
    return weighted_polling_average(shim, as_of, cfg)
