"""Score a resolved race's forecast series at each standard horizon (spec section 34).

For horizon ``h`` (days before the election), the target timestamp is ``election_date - h days``.
The observation used is the **latest observation at or before that target** -- so a 30-days-before
score never sees a poll, a model run, or an outcome from inside the final 30 days. A horizon with
no observation before its target is simply not scored (no fabricated point).

``forecast_scores`` rows are a pure function of immutable forecasts + an immutable resolution, so
re-scoring is idempotent: an existing row for ``(race_id, series, horizon_days)`` is updated in
place to the (identical) recomputed value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models_quant import ForecastResolution, ForecastScore, Race
from app.eval.metrics import STANDARD_HORIZONS, brier, log_loss
from app.eval.series import Observation, collect_series


@dataclass(frozen=True)
class ScoredPoint:
    race_id: str
    series: str
    horizon_days: int
    forecast_probability: float
    outcome: float
    brier_score: float
    log_loss: float
    forecast_observed_at: datetime
    methodology_version: Optional[str]


def nearest_observation(
    observations: Sequence[Observation], target_ts: datetime
) -> Optional[Observation]:
    """Latest observation at or before ``target_ts`` (point-in-time; no future information)."""
    eligible = [o for o in observations if o.observed_at <= target_ts]
    if not eligible:
        return None
    return max(eligible, key=lambda o: o.observed_at)


def _target_ts(election_date, horizon_days: int) -> datetime:
    # end of the day that is `horizon_days` before the election
    d = election_date - timedelta(days=horizon_days)
    return datetime.combine(d, time(23, 59, 59), tzinfo=timezone.utc)


def score_resolved_race(
    session: Session,
    race_id: str,
    *,
    horizons: Sequence[int] = STANDARD_HORIZONS,
    persist: bool = True,
) -> list[ScoredPoint]:
    """Score every series at every horizon for a race that has a recorded resolution.

    Returns the scored points. If ``persist`` is True, upserts ``forecast_scores`` rows.
    Returns ``[]`` (and writes nothing) if the race has no resolution yet.
    """
    race = session.execute(select(Race).where(Race.race_id == race_id)).scalar_one_or_none()
    resolution = session.execute(
        select(ForecastResolution).where(ForecastResolution.race_id == race_id)
    ).scalar_one_or_none()
    if race is None or resolution is None:
        return []

    outcome = 1.0 if float(resolution.dem_won) >= 0.5 else 0.0
    # outcome above is P(DEM wins); flip to the contract's YES party if needed
    if (race.contract_yes_party or "DEM").upper() == "REP":
        outcome = 1.0 - outcome

    by_series = collect_series(session, race_id)
    points: list[ScoredPoint] = []

    for series, observations in by_series.items():
        if not observations:
            continue
        for h in horizons:
            obs = nearest_observation(observations, _target_ts(race.election_date, h))
            if obs is None:
                continue
            p = obs.probability
            pt = ScoredPoint(
                race_id=race_id,
                series=series,
                horizon_days=h,
                forecast_probability=p,
                outcome=outcome,
                brier_score=brier(p, outcome),
                log_loss=log_loss(p, outcome),
                forecast_observed_at=obs.observed_at,
                methodology_version=obs.methodology_version,
            )
            points.append(pt)
            if persist:
                _upsert_score(session, pt)

    if persist:
        session.flush()
    return points


def _upsert_score(session: Session, pt: ScoredPoint) -> None:
    row = session.execute(
        select(ForecastScore).where(
            ForecastScore.race_id == pt.race_id,
            ForecastScore.series == pt.series,
            ForecastScore.horizon_days == pt.horizon_days,
        )
    ).scalar_one_or_none()
    if row is None:
        row = ForecastScore(race_id=pt.race_id, series=pt.series, horizon_days=pt.horizon_days)
        session.add(row)
    row.forecast_probability = pt.forecast_probability
    row.outcome = pt.outcome
    row.brier_score = pt.brier_score
    row.log_loss = pt.log_loss
    row.forecast_observed_at = pt.forecast_observed_at
    row.methodology_version = pt.methodology_version
    row.computed_at = datetime.now(timezone.utc)


def score_all_resolved(session: Session, *, horizons: Sequence[int] = STANDARD_HORIZONS) -> dict[str, int]:
    """Score every race that has a resolution. Returns ``{race_id: points_written}``."""
    resolved = session.execute(select(ForecastResolution.race_id)).scalars().all()
    summary: dict[str, int] = {}
    for rid in resolved:
        summary[rid] = len(score_resolved_race(session, rid, horizons=horizons, persist=True))
    return summary
