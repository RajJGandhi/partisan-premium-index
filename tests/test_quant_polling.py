from __future__ import annotations

import math
from datetime import date

import pytest

from app.quant.config import QUANT_V1
from app.quant.polling import (
    population_weight,
    quality_weight,
    recency_weight,
    sample_weight,
    sponsor_weight,
    weighted_polling_average,
)


def test_recency_weight_halves_every_half_life():
    hl = QUANT_V1.poll_half_life_days
    assert recency_weight(0, hl) == 1.0
    assert recency_weight(hl, hl) == pytest.approx(0.5)
    assert recency_weight(2 * hl, hl) == pytest.approx(0.25)
    assert recency_weight(-5, hl) == 1.0  # future-dated clamped to age 0


def test_sample_weight_is_sqrt_ratio_with_floor_and_cap():
    assert sample_weight(600) == pytest.approx(1.0)
    assert sample_weight(2400) == pytest.approx(2.0)
    # cap: 20000 -> capped at sqrt(5000/600)
    assert sample_weight(20000) == pytest.approx(math.sqrt(QUANT_V1.sample_weight_cap_n / 600))
    # floor: 10 -> floored at sqrt(100/600)
    assert sample_weight(10) == pytest.approx(math.sqrt(QUANT_V1.sample_weight_floor_n / 600))
    # unknown sample size -> neutral weight 1.0
    assert sample_weight(None) == pytest.approx(1.0)


def test_population_quality_sponsor_weights_match_config():
    assert population_weight("LV") == 1.00
    assert population_weight("RV") == 0.90
    assert population_weight("A") == 0.75
    assert population_weight(None) == 0.85
    assert quality_weight("A-") == 1.10
    assert quality_weight("B+") == 1.00
    assert quality_weight("C") == 0.85
    assert sponsor_weight(partisan_sponsor="X", internal=False) == 0.80
    assert sponsor_weight(partisan_sponsor=None, internal=True) == 0.75


def test_weighted_average_matches_hand_computation(make_poll):
    as_of = date(2026, 8, 27)
    polls = [
        make_poll(pollster="A", end_date=date(2026, 8, 25), dem_pct=52, rep_pct=44),
        make_poll(pollster="B", end_date=date(2026, 8, 25), dem_pct=48, rep_pct=48),
    ]
    avg = weighted_polling_average(polls, as_of, QUANT_V1)
    # identical everything except the margins -> weights equal -> simple mean of +8 and 0 = +4
    assert avg.polling_margin == pytest.approx(4.0)
    assert avg.used_poll_count == 2
    assert avg.pollster_diversity == 2
    assert avg.n_eff == pytest.approx(2.0)  # two equal weights -> n_eff = 2


def test_empty_polls_returns_none_margin():
    avg = weighted_polling_average([], date(2026, 8, 27), QUANT_V1)
    assert avg.polling_margin is None
    assert avg.n_eff == 0.0
    assert avg.used_poll_count == 0


def test_future_dated_polls_are_excluded(make_poll):
    avg = weighted_polling_average(
        [make_poll(end_date=date(2027, 1, 1))], date(2026, 8, 27), QUANT_V1
    )
    assert avg.polling_margin is None
    assert avg.raw_poll_count == 1
    assert avg.used_poll_count == 0


def test_n_eff_drops_when_one_poll_dominates(make_poll):
    as_of = date(2026, 8, 27)
    polls = [
        make_poll(pollster="Big", end_date=as_of, sample_size=4000, dem_pct=55, rep_pct=40),
        make_poll(pollster="Small", end_date=date(2026, 6, 1), sample_size=300, dem_pct=45, rep_pct=50),
    ]
    avg = weighted_polling_average(polls, as_of, QUANT_V1)
    assert 1.0 <= avg.n_eff < 2.0
    # margin pulled toward the heavier, newer poll (+15) and away from the old light one (-5)
    assert avg.polling_margin > 5.0


def test_pollster_flooding_downweights_repeat_releases(make_poll):
    as_of = date(2026, 8, 27)
    single = [make_poll(pollster="Solo", end_date=date(2026, 8, 26), dem_pct=60, rep_pct=40)]
    flooder = [
        make_poll(pollster="Flood", end_date=date(2026, 8, 26), dem_pct=60, rep_pct=40),
        make_poll(pollster="Flood", end_date=date(2026, 8, 25), dem_pct=60, rep_pct=40),
        make_poll(pollster="Flood", end_date=date(2026, 8, 24), dem_pct=60, rep_pct=40),
        make_poll(pollster="Flood", end_date=date(2026, 8, 23), dem_pct=60, rep_pct=40),
    ]
    a = weighted_polling_average(single, as_of, QUANT_V1)
    b = weighted_polling_average(flooder, as_of, QUANT_V1)
    # four near-duplicate releases must not add four polls' worth of effective weight
    assert b.n_eff < 3.0
    # geometric 0.5 decay caps the flood: total weight stays below two independent solo polls
    assert b.sum_weights < 2 * a.sum_weights


def test_per_poll_breakdown_is_exposed(make_poll):
    avg = weighted_polling_average([make_poll()], date(2026, 8, 27), QUANT_V1)
    assert len(avg.per_poll) == 1
    bd = avg.per_poll[0]["weight_breakdown"]
    assert set(bd) == {"recency", "sample", "population", "quality", "sponsor", "flooding"}
