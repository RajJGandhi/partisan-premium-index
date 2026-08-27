from __future__ import annotations

import pytest

from app.quant.senate_control import SenateRaceDistribution, simulate_senate_control


def _dist(mu, sigma=5.0, rid="r"):
    return SenateRaceDistribution(race_id=rid, mu=mu, sigma_total=sigma)


def test_deterministic_given_seed():
    races = [_dist(1.0), _dist(-2.0), _dist(0.5)]
    a = simulate_senate_control(races, holdover_dem=47, holdover_rep=48, tie_break_party="DEM",
                                n_sims=5000, seed=123)
    b = simulate_senate_control(races, holdover_dem=47, holdover_rep=48, tie_break_party="DEM",
                                n_sims=5000, seed=123)
    assert a.p_dem_control == b.p_dem_control


def test_probabilities_sum_to_one_and_distribution_normalised():
    races = [_dist(2.0), _dist(-1.0), _dist(0.0), _dist(3.0)]
    r = simulate_senate_control(races, holdover_dem=46, holdover_rep=46, tie_break_party="REP",
                                n_sims=20000, seed=7)
    assert r.p_dem_control + r.p_rep_control == pytest.approx(1.0)
    assert sum(r.dem_seat_distribution.values()) == pytest.approx(1.0, abs=1e-9)


def test_all_safe_dem_races_gives_near_certain_dem_control():
    races = [_dist(30.0, sigma=3.0, rid=f"r{i}") for i in range(5)]  # ~D+30 each
    r = simulate_senate_control(races, holdover_dem=46, holdover_rep=49, tie_break_party="REP",
                                n_sims=10000, seed=1)
    # 46 + 5 = 51 -> Dem majority in essentially every sim
    assert r.p_dem_control > 0.99


def test_tie_break_party_decides_the_50_50_mass():
    # construct so exactly 50-50 is common: 49 Dem holdovers + 1 near-coinflip race
    races = [_dist(0.0, sigma=6.0)]
    dem_vp = simulate_senate_control(races, holdover_dem=49, holdover_rep=50, tie_break_party="DEM",
                                     n_sims=40000, seed=42)
    rep_vp = simulate_senate_control(races, holdover_dem=49, holdover_rep=50, tie_break_party="REP",
                                     n_sims=40000, seed=42)
    # same seed, same draws; only the 50-50 tie assignment differs
    assert dem_vp.p_dem_control > rep_vp.p_dem_control
    assert dem_vp.p_dem_control == pytest.approx(0.5, abs=0.05)
    assert rep_vp.p_dem_control == pytest.approx(0.0, abs=0.02)


def test_correlated_national_error_widens_seat_spread():
    races = [_dist(0.0, sigma=5.0, rid=f"r{i}") for i in range(8)]
    indep = simulate_senate_control(races, holdover_dem=46, holdover_rep=46, tie_break_party="REP",
                                    n_sims=20000, seed=5, correlated_national_error_sd=0.0)
    corr = simulate_senate_control(races, holdover_dem=46, holdover_rep=46, tie_break_party="REP",
                                   n_sims=20000, seed=5, correlated_national_error_sd=6.0)

    def variance(dist):
        mean = sum(k * p for k, p in dist.items())
        return sum(p * (k - mean) ** 2 for k, p in dist.items())

    assert variance(corr.dem_seat_distribution) > variance(indep.dem_seat_distribution)


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        simulate_senate_control([_dist(1.0)], holdover_dem=-1, holdover_rep=0, tie_break_party="DEM")
    with pytest.raises(ValueError):
        simulate_senate_control([_dist(1.0)], holdover_dem=0, holdover_rep=0, tie_break_party="DEM", n_sims=0)
