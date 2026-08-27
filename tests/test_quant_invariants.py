"""Non-negotiable sanity invariants for the Quant model (spec section 17).

These test the *mathematics*, not a hard-coded winner: symmetry, monotonicity, and the two
sanity scenarios (strong-lead, toss-up).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app.quant.engine import run_quant_forecast
from app.quant.types import (
    CandidateInfo,
    NormalizedPoll,
    PresidentialResult,
    QuantForecastInput,
    RaceMeta,
    StateHistory,
)

ELECTION = date(2026, 11, 3)


def _mk(
    poll_specs,
    *,
    office="senate",
    dem_incumbent=False,
    rep_incumbent=False,
    days_to_election=75,
    state_margins=((2016, 2.0), (2020, 1.0), (2024, -2.0)),
    national_margins=((2016, 2.1), (2020, 4.5), (2024, -1.5)),
    mapping_confidence=1.0,
):
    """poll_specs: iterable of (dem_pct, rep_pct, days_before_as_of[, pollster])."""
    as_of = datetime.combine(ELECTION, datetime.min.time(), tzinfo=timezone.utc).replace(
        hour=13
    ) - timedelta(days=days_to_election)
    polls = []
    for i, spec in enumerate(poll_specs):
        dem, rep, ago = spec[0], spec[1], spec[2]
        pollster = spec[3] if len(spec) > 3 else f"Pollster {i}"
        polls.append(
            NormalizedPoll(
                pollster=pollster,
                end_date=as_of.date() - timedelta(days=ago),
                dem_pct=dem,
                rep_pct=rep,
                sample_size=800,
                population="LV",
                pollster_grade="B",
            )
        )
    return QuantForecastInput(
        race=RaceMeta(
            race_id="mm-sen-2026",
            state="MM",
            office=office,
            cycle=2026,
            election_date=ELECTION,
            dem_candidate=CandidateInfo("D", "DEM", is_incumbent=dem_incumbent),
            rep_candidate=CandidateInfo("R", "REP", is_incumbent=rep_incumbent),
        ),
        as_of=as_of,
        polls=tuple(polls),
        state_history=StateHistory(
            state="MM",
            state_results={y: PresidentialResult(y, dem_margin_pct=m) for y, m in state_margins},
            national_results={y: PresidentialResult(y, dem_margin_pct=m) for y, m in national_margins},
        ),
        candidate_mapping_confidence=mapping_confidence,
    )


def _mirror(inp: QuantForecastInput) -> QuantForecastInput:
    """Reflect every Democratic quantity into its Republican mirror image."""
    dem, rep = inp.race.dem_candidate, inp.race.rep_candidate
    mirrored_race = replace(
        inp.race,
        dem_candidate=replace(dem, is_incumbent=rep.is_incumbent) if dem and rep else dem,
        rep_candidate=replace(rep, is_incumbent=dem.is_incumbent) if dem and rep else rep,
    )
    return QuantForecastInput(
        race=mirrored_race,
        as_of=inp.as_of,
        polls=tuple(replace(p, dem_pct=p.rep_pct, rep_pct=p.dem_pct) for p in inp.polls),
        generic_ballot=tuple(
            replace(g, dem_pct=g.rep_pct, rep_pct=g.dem_pct) for g in inp.generic_ballot
        ),
        state_history=StateHistory(
            state=inp.state_history.state,
            state_results={
                y: PresidentialResult(y, dem_margin_pct=-r.margin)
                for y, r in inp.state_history.state_results.items()
            },
            national_results={
                y: PresidentialResult(y, dem_margin_pct=-r.margin)
                for y, r in inp.state_history.national_results.items()
            },
        ),
        national_environment_override=(
            None
            if inp.national_environment_override is None
            else -inp.national_environment_override
        ),
        candidate_mapping_confidence=inp.candidate_mapping_confidence,
    )


# --- SYMMETRY --------------------------------------------------------------------------------------
@pytest.mark.parametrize("dem_inc,rep_inc", [(False, False), (True, False), (False, True)])
def test_symmetry_p_dem_becomes_one_minus_p_dem(dem_inc, rep_inc):
    inp = _mk(
        [(53, 44, 5, "A"), (51, 46, 10, "B"), (52, 45, 15, "C")],
        dem_incumbent=dem_inc,
        rep_incumbent=rep_inc,
    )
    r = run_quant_forecast(inp)
    m = run_quant_forecast(_mirror(inp))
    assert not r.abstained and not m.abstained
    assert m.p_dem_win == pytest.approx(1.0 - r.p_dem_win, abs=1e-9)
    assert m.expected_margin == pytest.approx(-r.expected_margin, abs=1e-9)
    assert m.uncertainty.sigma_total == pytest.approx(r.uncertainty.sigma_total, abs=1e-9)


def test_symmetric_tossup_stays_50():
    inp = _mk(
        [(48, 48, 5, "A"), (47, 47, 12, "B")],
        state_margins=((2016, 0.0), (2020, 0.0), (2024, 0.0)),
        national_margins=((2016, 0.0), (2020, 0.0), (2024, 0.0)),
    )
    assert run_quant_forecast(inp).p_dem_win == pytest.approx(0.5, abs=1e-9)


# --- MONOTONICITY ---------------------------------------------------------------------------------
def test_increasing_dem_polling_margin_never_decreases_dem_probability():
    base = [(50, 46, 5, "A"), (49, 47, 11, "B")]
    p_prev = run_quant_forecast(_mk(base)).p_dem_win
    for bump in (1, 2, 4, 8, 12):
        shifted = [(d + bump, r - bump, ago, name) for (d, r, ago, name) in base]
        p = run_quant_forecast(_mk(shifted)).p_dem_win
        assert p >= p_prev - 1e-12
        p_prev = p


def test_reducing_uncertainty_with_positive_margin_never_decreases_dem_probability():
    specs = [(52, 46, 5, "A"), (51, 47, 12, "B"), (53, 45, 19, "C")]
    p_prev = None
    for days in (150, 120, 90, 60, 30, 14, 7):
        r = run_quant_forecast(_mk(specs, days_to_election=days))
        assert r.expected_margin > 0
        if p_prev is not None:
            assert r.p_dem_win >= p_prev - 1e-9
        p_prev = r.p_dem_win


def test_more_polls_at_same_lead_does_not_reduce_dem_probability_when_ahead():
    one = [(52, 46, 5, "A")]
    many = one + [(52, 46, 6, f"P{i}") for i in range(6)]
    r1 = run_quant_forecast(_mk(one))
    r2 = run_quant_forecast(_mk(many))
    assert r2.expected_margin >= r1.expected_margin - 1e-9
    assert r2.p_dem_win >= r1.p_dem_win - 1e-9


# --- STRONG-LEAD SANITY -------------------------------------------------------------------------
def test_strong_lead_produces_clearly_favoured_probability():
    specs = [
        (53, 45, 3, "A"),
        (54, 45, 5, "B"),
        (52, 45, 8, "C"),
        (53, 46, 11, "D"),
        (54, 44, 14, "E"),
        (52, 45, 18, "F"),
    ]
    r = run_quant_forecast(
        _mk(
            specs,
            days_to_election=75,
            state_margins=((2016, 3.0), (2020, 2.0), (2024, 1.0)),
            national_margins=((2016, 2.1), (2020, 4.5), (2024, -1.5)),
        )
    )
    assert r.polling_margin == pytest.approx(8.0, abs=1.5)
    assert 2.0 < r.expected_margin < 9.0
    assert r.p_dem_win > 0.80  # clearly favoured -- definitely not an absurd ~0.42


# --- TOSS-UP SANITY -----------------------------------------------------------------------------
def test_tossup_expected_margin_near_zero_yields_near_50():
    specs = [(48, 48, 5, "A"), (47, 48, 11, "B"), (49, 47, 17, "C")]
    r = run_quant_forecast(
        _mk(
            specs,
            state_margins=((2016, 0.5), (2020, -0.5), (2024, 0.0)),
            national_margins=((2016, 0.5), (2020, -0.5), (2024, 0.0)),
        )
    )
    assert abs(r.expected_margin) < 1.5
    assert 0.42 < r.p_dem_win < 0.58
