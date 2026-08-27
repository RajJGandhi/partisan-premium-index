from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.models_quant import ForecastResolution, ForecastScore, Race
from app.eval.calibration import build_calibration_report
from app.eval.leadlag import leadlag_analysis
from app.eval.series import Observation


# --- calibration ----------------------------------------------------------------------------
def _seed_scores(session, rows):
    """rows: (race_id, office, state, series, horizon, p, y)"""
    seen_races = set()
    for race_id, office, state, series, h, p, y in rows:
        if race_id not in seen_races:
            session.add(Race(race_id=race_id, state=state, office=office, cycle=2026,
                             election_date=date(2026, 11, 3), contract_yes_party="DEM"))
            session.add(ForecastResolution(race_id=race_id, dem_won=y))
            seen_races.add(race_id)
        session.add(ForecastScore(race_id=race_id, series=series, horizon_days=h,
                                  forecast_probability=p, outcome=y, brier_score=(p - y) ** 2,
                                  log_loss=0.1, methodology_version="ppi-quant-v1.0"))
    session.flush()


def test_report_groups_and_reports_n(quant_db):
    with quant_db() as s:
        _seed_scores(s, [
            ("r1", "senate", "NC", "quant", 30, 0.8, 1.0),
            ("r1", "senate", "NC", "market", 30, 0.7, 1.0),
            ("r2", "governor", "MI", "quant", 30, 0.4, 0.0),
            ("r2", "governor", "MI", "market", 30, 0.55, 0.0),
        ])
        s.commit()
        rep = build_calibration_report(s, group_by=("series", "horizon_days"))
        assert rep.n_score_rows == 4 and rep.n_resolved_races == 2
        groups = {(g.key["series"], g.key["horizon_days"]): g for g in rep.groups}
        assert groups[("quant", 30)].metrics.n == 2
        assert groups[("market", 30)].metrics.n == 2
        assert rep.overall.low_confidence is True  # tiny sample
        assert rep.overall.mean_brier is not None

        by_office = build_calibration_report(s, group_by=("office",))
        assert {g.key["office"] for g in by_office.groups} == {"senate", "governor"}


def test_report_comparisons_pair_by_race_and_horizon(quant_db):
    with quant_db() as s:
        _seed_scores(s, [
            ("r1", "senate", "NC", "quant", 30, 0.60, 1.0),   # brier .16
            ("r1", "senate", "NC", "ensemble", 30, 0.80, 1.0),  # brier .04 -> ensemble better
            ("r2", "senate", "GA", "quant", 30, 0.50, 0.0),   # brier .25
            ("r2", "senate", "GA", "ensemble", 30, 0.40, 0.0),  # brier .16 -> ensemble better
        ])
        s.commit()
        rep = build_calibration_report(s)
        cmp = rep.comparisons["ensemble_vs_quant"]
        assert cmp["n"] == 2
        assert cmp["mean_brier_delta"] < 0  # ensemble lower Brier on average
        assert cmp["a_better_share"] == 1.0
        assert "partisan_asymmetry" in rep.comparisons


def test_unknown_group_dimension_rejected(quant_db):
    with quant_db() as s:
        with pytest.raises(ValueError):
            build_calibration_report(s, group_by=("liquidity_bucket",))


# --- lead / lag -----------------------------------------------------------------------------
def _series(name, start_probs):
    base = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    return [Observation(name, base + timedelta(days=i), p) for i, p in enumerate(start_probs)]


def test_leadlag_detects_market_leading_quant():
    # quant is market shifted one day later
    market_probs = [0.50, 0.52, 0.55, 0.58, 0.60, 0.63, 0.66, 0.68, 0.70, 0.72]
    quant_probs = [0.50, 0.50, 0.52, 0.55, 0.58, 0.60, 0.63, 0.66, 0.68, 0.70]
    res = leadlag_analysis(_series("market", market_probs), _series("quant", quant_probs),
                           series_a="market", series_b="quant", min_points=6)
    assert res.classification == "A_leads"
    assert res.best_lag >= 1


def test_leadlag_synchronous():
    probs = [0.5, 0.53, 0.55, 0.58, 0.6, 0.63, 0.65, 0.68]
    res = leadlag_analysis(_series("a", probs), _series("b", probs), min_points=5)
    assert res.classification == "synchronous"
    assert res.best_lag == 0


def test_leadlag_insufficient_data():
    res = leadlag_analysis(_series("a", [0.5, 0.6]), _series("b", [0.5, 0.6]), min_points=6)
    assert res.classification == "insufficient_data" and res.best_lag is None
