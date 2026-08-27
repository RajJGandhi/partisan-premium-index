"""Point-in-time backtesting for PPI Quant (spec section 47).

``ppi backtest --cycle 2024`` re-runs the deterministic model as if it were standing ``h`` days
before each race's election, and (where the outcome is known) scores that forecast. The
:class:`PointInTimeGuard` enforces the core discipline: **no lookahead**. A backtest at horizon
``h`` may not read a poll conducted after the cutoff, later candidate information, the final
margin, or the resolution -- the guard drops (or, in strict mode, raises on) any datum dated after
the cutoff, and the resolution is only ever used to score the model's *output*, never fed into its
input.

Works today on a races config file (inline polls / history / generic ballot / resolution); the
same guard wraps the live provider chains when real historical datasets are wired.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence, TypeVar

from app.eval.metrics import STANDARD_HORIZONS, brier, log_loss
from app.quant.config import QUANT_V1, MethodologyConfig
from app.quant.engine import run_quant_forecast
from app.quant.types import (
    CandidateInfo,
    GenericBallotPoll,
    NormalizedPoll,
    PresidentialResult,
    QuantForecastInput,
    RaceMeta,
    StateHistory,
)

T = TypeVar("T")


class PointInTimeError(RuntimeError):
    """A datum dated after the backtest cutoff was encountered in strict mode (lookahead leak)."""


@dataclass
class PointInTimeGuard:
    as_of: datetime
    strict: bool = False
    dropped: int = 0
    drop_log: list[str] = field(default_factory=list)

    @property
    def as_of_date(self) -> date:
        return self.as_of.date()

    def allow(self, item_date: date | datetime | None, label: str) -> bool:
        if item_date is None:
            return True  # undated -> caller decides; we don't fabricate a date
        d = item_date.date() if isinstance(item_date, datetime) else item_date
        if d <= self.as_of_date:
            return True
        self.dropped += 1
        self.drop_log.append(f"{label} dated {d.isoformat()} > cutoff {self.as_of_date.isoformat()}")
        if self.strict:
            raise PointInTimeError(self.drop_log[-1])
        return False

    def filter(self, items: Iterable[T], key: Callable[[T], date | datetime | None], label: str) -> list[T]:
        return [it for it in items if self.allow(key(it), label)]


@dataclass(frozen=True)
class BacktestPoint:
    race_id: str
    horizon_days: int
    as_of: str
    forecast_probability: Optional[float]  # P(Dem win), None if the model abstained
    data_quality: str
    abstained: bool
    dropped_future_data: int
    outcome: Optional[float] = None  # P(Dem win) outcome, if resolved
    brier_score: Optional[float] = None
    log_loss: Optional[float] = None


@dataclass(frozen=True)
class BacktestReport:
    cycle: int
    model_version: str
    strict: bool
    points: tuple[BacktestPoint, ...]
    by_horizon: dict[int, dict]
    n_races: int
    n_scored: int

    def as_dict(self) -> dict:
        return {
            "cycle": self.cycle,
            "model_version": self.model_version,
            "strict": self.strict,
            "n_races": self.n_races,
            "n_scored": self.n_scored,
            "by_horizon": self.by_horizon,
            "points": [p.__dict__ for p in self.points],
        }


def _pd(v: Any) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _as_of_ts(election_date: date, horizon_days: int) -> datetime:
    return datetime.combine(election_date - timedelta(days=horizon_days), time(13, 0), tzinfo=timezone.utc)


def _build_pit_input(race_cfg: dict, guard: PointInTimeGuard, cycle: int) -> QuantForecastInput:
    state = race_cfg["state"].upper()[:2]
    election_date = _pd(race_cfg["election_date"]) or date(cycle, 11, 3)

    polls = guard.filter(race_cfg.get("polls", []), lambda p: _pd(p.get("end_date")), f"{race_cfg['race_id']} poll")
    norm_polls = tuple(
        NormalizedPoll(
            pollster=str(p.get("pollster") or "Unknown"),
            end_date=_pd(p["end_date"]),  # type: ignore[arg-type]
            dem_pct=float(p["dem_pct"]),
            rep_pct=float(p["rep_pct"]),
            start_date=_pd(p.get("start_date")),
            sample_size=p.get("sample_size"),
            population=p.get("population"),
            pollster_grade=p.get("pollster_grade"),
            partisan_sponsor=p.get("partisan_sponsor"),
        )
        for p in polls
        if _pd(p.get("end_date"))
    )
    gb = guard.filter(race_cfg.get("generic_ballot", []), lambda g: _pd(g.get("end_date")), "generic_ballot")
    norm_gb = tuple(
        GenericBallotPoll(
            pollster=str(g.get("pollster") or "Unknown"),
            end_date=_pd(g["end_date"]),  # type: ignore[arg-type]
            dem_pct=float(g["dem_pct"]),
            rep_pct=float(g["rep_pct"]),
            sample_size=g.get("sample_size"),
            population=g.get("population"),
            pollster_grade=g.get("pollster_grade"),
        )
        for g in gb
        if _pd(g.get("end_date"))
    )

    hist_raw = race_cfg.get("state_history") or {}
    sr = {
        int(y): PresidentialResult(int(y), dem_margin_pct=float(m))
        for y, m in hist_raw.get("state_results", {}).items()
        if int(y) < cycle  # a backtest of cycle C must not use cycle C's own presidential result
    }
    nr = {
        int(y): PresidentialResult(int(y), dem_margin_pct=float(m))
        for y, m in hist_raw.get("national_results", {}).items()
        if int(y) < cycle
    }
    state_history = StateHistory(state=state, state_results=sr, national_results=nr) if sr and nr else None

    def _cand(raw, party):
        if not raw:
            return None
        if isinstance(raw, str):
            raw = {"name": raw}
        return CandidateInfo(
            name=raw.get("name", ""), party=party,
            is_incumbent=bool(raw.get("is_incumbent", False)),
            status=raw.get("status", "confirmed"),
        )

    return QuantForecastInput(
        race=RaceMeta(
            race_id=race_cfg["race_id"], state=state, office=race_cfg["office"], cycle=cycle,
            election_date=election_date,
            dem_candidate=_cand(race_cfg.get("dem_candidate"), "DEM"),
            rep_candidate=_cand(race_cfg.get("rep_candidate"), "REP"),
        ),
        as_of=guard.as_of,
        polls=norm_polls,
        generic_ballot=norm_gb,
        state_history=state_history,
        candidate_mapping_confidence=float(race_cfg.get("candidate_mapping_confidence", 1.0)),
    )


def run_backtest(
    races_config: Sequence[dict],
    *,
    cycle: int,
    model_version: str | None = None,
    horizons: Sequence[int] = STANDARD_HORIZONS,
    resolutions: dict[str, dict] | None = None,
    strict: bool = False,
    cfg: MethodologyConfig = QUANT_V1,
) -> BacktestReport:
    """Backtest PPI Quant for one cycle. ``resolutions`` maps race_id -> {dem_won, final_margin_dem?}.

    The resolution is used **only** to score the model output; it never enters
    :func:`_build_pit_input`.
    """
    model_version = model_version or cfg.version
    resolutions = resolutions or {}
    points: list[BacktestPoint] = []
    scored = 0

    for race_cfg in races_config:
        rid = race_cfg["race_id"]
        election_date = _pd(race_cfg["election_date"]) or date(cycle, 11, 3)
        res = resolutions.get(rid)
        outcome = None
        if res is not None:
            outcome = 1.0 if (res.get("dem_won") is True or float(res.get("dem_won", 0)) >= 0.5) else 0.0

        for h in horizons:
            guard = PointInTimeGuard(as_of=_as_of_ts(election_date, h), strict=strict)
            inp = _build_pit_input(race_cfg, guard, cycle)
            result = run_quant_forecast(inp, cfg)
            p = None if result.abstained else result.p_dem_win
            pt = BacktestPoint(
                race_id=rid,
                horizon_days=h,
                as_of=guard.as_of.date().isoformat(),
                forecast_probability=p,
                data_quality=result.data_quality,
                abstained=result.abstained,
                dropped_future_data=guard.dropped,
                outcome=outcome,
                brier_score=(brier(p, outcome) if (p is not None and outcome is not None) else None),
                log_loss=(log_loss(p, outcome) if (p is not None and outcome is not None) else None),
            )
            points.append(pt)
            if pt.brier_score is not None:
                scored += 1

    by_horizon: dict[int, dict] = {}
    for h in horizons:
        hp = [p for p in points if p.horizon_days == h and p.brier_score is not None]
        by_horizon[h] = {
            "n": len(hp),
            "mean_brier": (sum(p.brier_score for p in hp) / len(hp)) if hp else None,  # type: ignore[misc]
            "abstain_rate": (
                sum(1 for p in points if p.horizon_days == h and p.abstained)
                / max(1, sum(1 for p in points if p.horizon_days == h))
            ),
        }

    return BacktestReport(
        cycle=cycle,
        model_version=model_version,
        strict=strict,
        points=tuple(points),
        by_horizon=by_horizon,
        n_races=len(races_config),
        n_scored=scored,
    )


def load_backtest_config(path: Path) -> tuple[list[dict], int, dict[str, dict]]:
    """Read a races JSON (same shape as data/seed/quant_example_races.json, optionally with a
    top-level ``resolutions`` array or per-race ``resolution`` object)."""
    doc = json.loads(Path(path).read_text())
    races = doc.get("races", [])
    cycle = int(races[0]["cycle"]) if races else 2026
    resolutions: dict[str, dict] = {r["race_id"]: r for r in doc.get("resolutions", [])}
    for r in races:
        if "resolution" in r:
            resolutions[r["race_id"]] = r["resolution"]
    return races, cycle, resolutions
