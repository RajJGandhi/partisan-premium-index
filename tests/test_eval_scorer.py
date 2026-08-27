from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.db.models_quant import ForecastScore, QuantForecast, Race
from app.eval.resolutions import record_resolution
from app.eval.scorer import nearest_observation, score_all_resolved, score_resolved_race
from app.eval.series import Observation


def _obs(day, p):
    return Observation("quant", datetime(2026, 10, day, 13, tzinfo=timezone.utc), p)


def test_nearest_observation_is_point_in_time():
    obs = [_obs(1, 0.5), _obs(10, 0.6), _obs(20, 0.7), _obs(31, 0.8)]
    target = datetime(2026, 10, 15, 23, 59, 59, tzinfo=timezone.utc)
    picked = nearest_observation(obs, target)
    assert picked.observed_at.day == 10  # latest AT OR BEFORE the target, never the day-20 point
    # nothing before the target -> None (no fabricated score)
    assert nearest_observation(obs, datetime(2026, 9, 1, tzinfo=timezone.utc)) is None
    # exact timestamp match is allowed
    assert nearest_observation([_obs(15, 0.55)], datetime(2026, 10, 15, 13, tzinfo=timezone.utc)).probability == 0.55


def _seed_race(session, *, race_id="tx-sen-2026", election=date(2026, 11, 3), contract_yes="DEM"):
    session.add(Race(race_id=race_id, state="TX", office="senate", cycle=2026, election_date=election,
                     contract_yes_party=contract_yes))
    session.flush()


def _seed_quant(session, race_id, generated_at, p):
    session.add(QuantForecast(
        race_id=race_id, run_key=f"k:{generated_at.date()}", methodology_version="ppi-quant-v1.0",
        config_hash="c" * 64, input_hash="i" * 64, generated_at=generated_at, data_quality="NORMAL",
        abstained=False, p_dem_win=p, p_dem_win_uncapped=p, p_rep_win=1 - p,
        pipeline_mode="shadow", publication_status="SHADOW",
    ))
    session.flush()


def test_no_resolution_means_no_scores(quant_db):
    with quant_db() as s:
        _seed_race(s)
        _seed_quant(s, "tx-sen-2026", datetime(2026, 9, 1, tzinfo=timezone.utc), 0.7)
        assert score_resolved_race(s, "tx-sen-2026") == []
        s.commit()
        assert s.query(ForecastScore).count() == 0


def test_scores_across_horizons_and_is_idempotent(quant_db):
    with quant_db() as s:
        _seed_race(s)  # election 2026-11-03 -> targets: 90d=Aug5, 60d=Sep4, 30d=Oct4, 14d=Oct20, 7d=Oct27, 1d=Nov2
        _seed_quant(s, "tx-sen-2026", datetime(2026, 7, 20, 13, tzinfo=timezone.utc), 0.62)  # before 90d target
        _seed_quant(s, "tx-sen-2026", datetime(2026, 8, 20, 13, tzinfo=timezone.utc), 0.71)  # between 90d and 60d
        _seed_quant(s, "tx-sen-2026", datetime(2026, 10, 24, 13, tzinfo=timezone.utc), 0.80)  # between 14d and 7d
        record_resolution(s, "tx-sen-2026", dem_won=1, final_margin_dem=5.0)
        s.commit()

        pts = score_resolved_race(s, "tx-sen-2026")
        s.commit()
        by_h = {p.horizon_days: p for p in pts if p.series == "quant"}
        assert set(by_h) == {90, 60, 30, 14, 7, 1}
        assert by_h[90].forecast_probability == 0.62  # only the Jul 20 forecast precedes the Aug 5 target
        assert by_h[60].forecast_probability == 0.71
        assert by_h[30].forecast_probability == 0.71
        assert by_h[14].forecast_probability == 0.71
        assert by_h[7].forecast_probability == 0.80
        assert by_h[1].forecast_probability == 0.80
        assert by_h[90].outcome == 1.0
        assert by_h[90].brier_score == pytest.approx((0.62 - 1) ** 2)

        n_before = s.query(ForecastScore).count()
        b_before = {r.horizon_days: r.brier_score for r in s.query(ForecastScore)}
        score_resolved_race(s, "tx-sen-2026")  # re-score
        s.commit()
        assert s.query(ForecastScore).count() == n_before  # no duplicate rows
        assert {r.horizon_days: r.brier_score for r in s.query(ForecastScore)} == b_before  # stable


def test_rep_contract_flips_outcome_and_probability(quant_db):
    with quant_db() as s:
        _seed_race(s, race_id="tx-sen-2026", contract_yes="REP")
        _seed_quant(s, "tx-sen-2026", datetime(2026, 7, 26, 13, tzinfo=timezone.utc), 0.62)  # P(Dem) 0.62
        record_resolution(s, "tx-sen-2026", dem_won=1)  # Dem won -> REP-contract YES outcome = 0
        s.commit()
        pts = score_resolved_race(s, "tx-sen-2026")
        p90 = next(p for p in pts if p.horizon_days == 90)
        assert p90.forecast_probability == pytest.approx(0.38)  # 1 - P(Dem)
        assert p90.outcome == 0.0


def test_score_all_resolved(quant_db):
    with quant_db() as s:
        for rid in ("a-sen-2026", "b-gov-2026"):
            _seed_race(s, race_id=rid)
            _seed_quant(s, rid, datetime(2026, 7, 1, 13, tzinfo=timezone.utc), 0.6)
            record_resolution(s, rid, dem_won=1)
        s.commit()
        summary = score_all_resolved(s)
        s.commit()
        assert set(summary) == {"a-sen-2026", "b-gov-2026"}
        assert all(v > 0 for v in summary.values())
