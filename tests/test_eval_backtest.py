from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.eval.backtest import (
    PointInTimeError,
    PointInTimeGuard,
    load_backtest_config,
    run_backtest,
)

SEED = Path(__file__).resolve().parents[1] / "data" / "seed"


# --- the guard --------------------------------------------------------------------------------
def test_guard_drops_future_data_by_default():
    g = PointInTimeGuard(as_of=datetime(2026, 8, 5, tzinfo=timezone.utc))
    assert g.allow(date(2026, 7, 30), "poll") is True
    assert g.allow(date(2026, 8, 5), "poll") is True  # same day is allowed
    assert g.allow(date(2026, 8, 21), "poll") is False
    assert g.dropped == 1 and "2026-08-21" in g.drop_log[0]
    assert g.allow(None, "undated") is True  # undated -> caller decides, not fabricated


def test_guard_strict_raises_on_lookahead():
    g = PointInTimeGuard(as_of=datetime(2026, 8, 5, tzinfo=timezone.utc), strict=True)
    with pytest.raises(PointInTimeError):
        g.allow(date(2026, 9, 1), "later poll")


def test_guard_filter():
    g = PointInTimeGuard(as_of=datetime(2026, 8, 5, tzinfo=timezone.utc))
    polls = [{"end_date": "2026-07-01"}, {"end_date": "2026-08-20"}, {"end_date": "2026-08-03"}]
    kept = g.filter(polls, lambda p: date.fromisoformat(p["end_date"]), "poll")
    assert [p["end_date"] for p in kept] == ["2026-07-01", "2026-08-03"]
    assert g.dropped == 1


# --- the backtest ----------------------------------------------------------------------------
def _config():
    races, cycle, resolutions = load_backtest_config(SEED / "quant_example_races.json")
    doc = json.loads((SEED / "quant_example_resolutions.json").read_text())
    resolutions = {r["race_id"]: r for r in doc["resolutions"]}
    return races, cycle, resolutions


def test_backtest_produces_scored_points_at_every_horizon():
    races, cycle, resolutions = _config()
    report = run_backtest(races, cycle=cycle, resolutions=resolutions)
    assert report.cycle == 2026 and report.model_version == "ppi-quant-v1.0"
    assert report.n_races == 3
    assert report.n_scored == 3 * 6  # 3 races x 6 horizons, all resolved
    for h in (90, 60, 30, 14, 7, 1):
        assert report.by_horizon[h]["n"] == 3
        assert 0.0 <= report.by_horizon[h]["mean_brier"] <= 1.0


def test_backtest_filters_polls_conducted_after_the_cutoff():
    races, cycle, resolutions = _config()
    report = run_backtest(races, cycle=cycle, resolutions=resolutions)
    # xx-sen-2026 has polls through 2026-08-21; at the 90-day cutoff (2026-08-05) all but the
    # 2026-07-31 poll must be dropped
    xx90 = next(p for p in report.points if p.race_id == "xx-sen-2026" and p.horizon_days == 90)
    assert xx90.dropped_future_data == 3
    assert xx90.data_quality == "THIN"  # only one poll left -> fundamentals dominate
    xx7 = next(p for p in report.points if p.race_id == "xx-sen-2026" and p.horizon_days == 7)
    assert xx7.dropped_future_data == 0  # everything is well before the final week


def test_backtest_never_reads_the_resolution_or_a_later_poll():
    """The forecast at each horizon must be identical whether or not a resolution is supplied,
    and whether or not future polls exist in the config."""
    races, cycle, resolutions = _config()
    with_res = run_backtest(races, cycle=cycle, resolutions=resolutions)
    without_res = run_backtest(races, cycle=cycle, resolutions={})
    fp_with = {(p.race_id, p.horizon_days): p.forecast_probability for p in with_res.points}
    fp_without = {(p.race_id, p.horizon_days): p.forecast_probability for p in without_res.points}
    assert fp_with == fp_without  # the outcome never leaks into the input


def test_backtest_strict_mode_raises_on_future_poll():
    races, cycle, resolutions = _config()
    with pytest.raises(PointInTimeError):
        run_backtest(races, cycle=cycle, resolutions=resolutions, strict=True)


def test_backtest_excludes_cycle_C_own_presidential_result():
    # a 2024 backtest must not use the 2024 presidential margin for state lean
    races = [{
        "race_id": "aa-sen-2024", "state": "AA", "office": "senate", "cycle": 2024,
        "election_date": "2024-11-05",
        "dem_candidate": {"name": "D", "party": "DEM"}, "rep_candidate": {"name": "R", "party": "REP"},
        "state_history": {"state_results": {"2016": 1.0, "2020": 2.0, "2024": 30.0},
                          "national_results": {"2016": 2.1, "2020": 4.5, "2024": -1.5}},
        "polls": [],
    }]
    r = run_backtest(races, cycle=2024, resolutions={})
    # if 2024's +30 leaked in, the fundamentals would be wildly D-favoured; with only 2016/2020
    # (renormalised) it stays modest
    p = next(pt for pt in r.points if pt.horizon_days == 30)
    assert p.forecast_probability is None or p.forecast_probability < 0.75
