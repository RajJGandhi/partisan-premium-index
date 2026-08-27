"""Calibration + comparison aggregation over ``forecast_scores`` (spec sections 35, 49).

Answers the research questions the schema must support:

- Are prediction markets better calibrated than PPI Quant? (per-series mean Brier / calibration)
- Are GPT or Claude better calibrated than the quant model? (per-series)
- Does the ensemble improve Brier? (``ensemble`` vs ``quant`` delta)
- When all independent models agree against the market, who is right? (``model_vs_market`` view)
- Are market-model spreads systematically different when the priced outcome favours D vs R?
- Does divergence shrink near resolution / with liquidity?

Every aggregate carries its N and a ``low_confidence`` flag; nothing claims significance on a tiny
sample.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models_quant import ForecastResolution, ForecastScore, Race
from app.eval.metrics import Aggregate, aggregate
from app.eval.series import SERIES_ENSEMBLE, SERIES_MARKET, SERIES_QUANT

GROUP_DIMENSIONS = ("series", "horizon_days", "office", "state", "methodology_version")


@dataclass(frozen=True)
class ScoreRow:
    race_id: str
    series: str
    horizon_days: int
    probability: float
    outcome: float
    brier: float
    log_loss: Optional[float]
    office: Optional[str]
    state: Optional[str]
    methodology_version: Optional[str]
    election_date: Optional[str]


def _load_rows(
    session: Session,
    *,
    series: Iterable[str] | None = None,
    horizon_days: int | None = None,
) -> list[ScoreRow]:
    stmt = select(ForecastScore, Race).join(Race, Race.race_id == ForecastScore.race_id)
    if series is not None:
        stmt = stmt.where(ForecastScore.series.in_(list(series)))
    if horizon_days is not None:
        stmt = stmt.where(ForecastScore.horizon_days == horizon_days)
    rows: list[ScoreRow] = []
    for sc, race in session.execute(stmt):
        rows.append(
            ScoreRow(
                race_id=sc.race_id,
                series=sc.series,
                horizon_days=sc.horizon_days,
                probability=float(sc.forecast_probability),
                outcome=float(sc.outcome),
                brier=float(sc.brier_score),
                log_loss=None if sc.log_loss is None else float(sc.log_loss),
                office=race.office,
                state=race.state,
                methodology_version=sc.methodology_version,
                election_date=race.election_date.isoformat() if race.election_date else None,
            )
        )
    return rows


@dataclass(frozen=True)
class GroupResult:
    key: dict
    metrics: Aggregate

    def as_dict(self) -> dict:
        return {"group": self.key, **self.metrics.as_dict()}


@dataclass(frozen=True)
class CalibrationReport:
    group_by: tuple[str, ...]
    groups: tuple[GroupResult, ...]
    overall: Aggregate
    n_score_rows: int
    n_resolved_races: int
    comparisons: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "group_by": list(self.group_by),
            "n_score_rows": self.n_score_rows,
            "n_resolved_races": self.n_resolved_races,
            "overall": self.overall.as_dict(),
            "groups": [g.as_dict() for g in self.groups],
            "comparisons": self.comparisons,
        }


def _group_key(row: ScoreRow, dims: tuple[str, ...]) -> tuple:
    return tuple(getattr(row, d) for d in dims)


def build_calibration_report(
    session: Session,
    *,
    group_by: tuple[str, ...] = ("series", "horizon_days"),
    series: Iterable[str] | None = None,
    horizon_days: int | None = None,
    n_bins: int = 10,
) -> CalibrationReport:
    for d in group_by:
        if d not in GROUP_DIMENSIONS:
            raise ValueError(f"unknown group dimension {d!r}; allowed: {GROUP_DIMENSIONS}")
    rows = _load_rows(session, series=series, horizon_days=horizon_days)
    n_resolved = session.execute(select(ForecastResolution.race_id)).scalars().all()

    buckets: dict[tuple, list[ScoreRow]] = {}
    for r in rows:
        buckets.setdefault(_group_key(r, group_by), []).append(r)

    groups = tuple(
        GroupResult(
            key=dict(zip(group_by, k, strict=True)),
            metrics=aggregate([(r.probability, r.outcome) for r in rs], n_bins=n_bins),
        )
        for k, rs in sorted(buckets.items(), key=lambda kv: [str(x) for x in kv[0]])
    )
    overall = aggregate([(r.probability, r.outcome) for r in rows], n_bins=n_bins)

    return CalibrationReport(
        group_by=group_by,
        groups=groups,
        overall=overall,
        n_score_rows=len(rows),
        n_resolved_races=len(n_resolved),
        comparisons=_comparisons(rows),
    )


def _comparisons(rows: list[ScoreRow]) -> dict:
    """Paired, per-horizon deltas that answer the section 49 research questions."""
    out: dict = {}

    def _paired_delta(series_a: str, series_b: str) -> dict:
        # by race_id + horizon so we only compare like-for-like observation distances
        a = {(r.race_id, r.horizon_days): r.brier for r in rows if r.series == series_a}
        b = {(r.race_id, r.horizon_days): r.brier for r in rows if r.series == series_b}
        common = sorted(set(a) & set(b))
        if not common:
            return {"n": 0, "mean_brier_delta": None, "a_better_share": None}
        deltas = [a[k] - b[k] for k in common]  # negative => series_a lower Brier (better)
        return {
            "n": len(common),
            "mean_brier_delta": sum(deltas) / len(deltas),
            "a_better_share": sum(1 for d in deltas if d < 0) / len(deltas),
            "low_confidence": len(common) < 20,
        }

    out["ensemble_vs_quant"] = _paired_delta(SERIES_ENSEMBLE, SERIES_QUANT)
    out["quant_vs_market"] = _paired_delta(SERIES_QUANT, SERIES_MARKET)
    out["ensemble_vs_market"] = _paired_delta(SERIES_ENSEMBLE, SERIES_MARKET)
    out["openai_vs_quant"] = _paired_delta("openai", SERIES_QUANT)
    out["anthropic_vs_quant"] = _paired_delta("anthropic", SERIES_QUANT)

    # partisan asymmetry: mean signed error (p - y) for each series, split by priced direction
    asym: dict = {}
    for series in {r.series for r in rows}:
        s_rows = [r for r in rows if r.series == series]
        favours_yes = [r.probability - r.outcome for r in s_rows if r.probability >= 0.5]
        favours_no = [r.probability - r.outcome for r in s_rows if r.probability < 0.5]
        asym[series] = {
            "n_favours_yes": len(favours_yes),
            "mean_signed_error_favours_yes": (sum(favours_yes) / len(favours_yes)) if favours_yes else None,
            "n_favours_no": len(favours_no),
            "mean_signed_error_favours_no": (sum(favours_no) / len(favours_no)) if favours_no else None,
        }
    out["partisan_asymmetry"] = asym
    return out
