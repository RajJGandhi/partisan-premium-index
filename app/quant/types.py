"""Typed data contracts for the Quant engine.

Everything the engine reads is a normalized, provider-agnostic dataclass here. In particular
:class:`QuantForecastInput` is the *only* thing ``run_quant_forecast`` accepts, and it is
structurally incapable of carrying a prediction-market price: see ``FORBIDDEN_INPUT_KEYS`` and
:func:`assert_market_free`, which are asserted by ``tests/test_quant_market_independence.py``.

All margins are in **expected vote-margin points, Democratic minus Republican** (D+5 -> ``+5.0``,
R+5 -> ``-5.0``), never in probability space, until the final :mod:`app.quant.probability` step.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Literal, Optional

Office = Literal["senate", "governor"]
Party = Literal["DEM", "REP", "OTHER"]

# Any of these appearing anywhere in an engine input is a hard error: the quantitative forecast
# must never see prediction-market information (spec sections 17, 18, 22). Kept broad on purpose.
FORBIDDEN_INPUT_KEYS: frozenset[str] = frozenset(
    {
        "market_probability",
        "polymarket_probability",
        "market_price",
        "comparison_price",
        "comparison_price_at_join",
        "yes_price",
        "no_price",
        "yes_price_displayed",
        "no_price_displayed",
        "yes_best_bid",
        "yes_best_ask",
        "no_best_bid",
        "no_best_ask",
        "best_bid",
        "best_ask",
        "yes_midpoint",
        "no_midpoint",
        "midpoint",
        "last_trade_price",
        "spread",
        "depth_1c",
        "depth_3c",
        "depth_5c",
        "volume",
        "liquidity",
        "open_interest",
        "executable_buy_price",
        "executable_sell_price",
        "market_history",
        "kalshi_price",
        "predictit_price",
        "betting_odds",
        "implied_probability",
        "raw_ppi",
        "partisan_premium",
        "market_model_spread",
    }
)


def assert_market_free(obj: Any, *, path: str = "input") -> None:
    """Recursively raise if *obj* contains any :data:`FORBIDDEN_INPUT_KEYS` mapping key.

    Called by the engine on every forecast before any calculation runs. A future field addition
    that accidentally threads a market price into the Quant path fails loudly here rather than
    silently contaminating the deterministic forecast.
    """

    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and key.strip().lower() in FORBIDDEN_INPUT_KEYS:
                raise ValueError(
                    f"prediction-market field '{key}' is not allowed in the Quant forecast "
                    f"input (at {path}); the quantitative model must stay blind to market data"
                )
            assert_market_free(value, path=f"{path}.{key}")
    elif isinstance(obj, (list, tuple)):
        for i, value in enumerate(obj):
            assert_market_free(value, path=f"{path}[{i}]")


def _stable_hash(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------------------
# Normalized inputs
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class NormalizedPoll:
    """One individual race poll, normalized across providers (spec section 6)."""

    pollster: str
    end_date: date
    dem_pct: float
    rep_pct: float
    start_date: Optional[date] = None
    sample_size: Optional[int] = None
    population: Optional[str] = None  # LV / RV / A / None
    pollster_grade: Optional[str] = None
    partisan_sponsor: Optional[str] = None
    internal: bool = False
    poll_id: Optional[str] = None
    source: Optional[str] = None

    @property
    def margin(self) -> float:
        """Democratic margin in points: ``dem_pct - rep_pct``."""
        return float(self.dem_pct) - float(self.rep_pct)

    def age_days(self, as_of: date) -> float:
        return max(0.0, (as_of - self.end_date).days)


@dataclass(frozen=True)
class GenericBallotPoll:
    """One national generic-congressional-ballot poll (spec section 10)."""

    pollster: str
    end_date: date
    dem_pct: float
    rep_pct: float
    sample_size: Optional[int] = None
    population: Optional[str] = None
    pollster_grade: Optional[str] = None
    partisan_sponsor: Optional[str] = None
    internal: bool = False
    source: Optional[str] = None

    @property
    def margin(self) -> float:
        return float(self.dem_pct) - float(self.rep_pct)

    def age_days(self, as_of: date) -> float:
        return max(0.0, (as_of - self.end_date).days)


@dataclass(frozen=True)
class PresidentialResult:
    """A single state-or-nation presidential result, used to build state lean (spec section 9)."""

    year: int
    dem_votes: Optional[float] = None
    rep_votes: Optional[float] = None
    dem_margin_pct: Optional[float] = None  # if provided, used directly (Dem - Rep, points)

    @property
    def margin(self) -> float:
        if self.dem_margin_pct is not None:
            return float(self.dem_margin_pct)
        if self.dem_votes is None or self.rep_votes is None:
            raise ValueError("PresidentialResult needs dem_margin_pct or dem_votes+rep_votes")
        total = float(self.dem_votes) + float(self.rep_votes)
        if total <= 0:
            raise ValueError("PresidentialResult has non-positive two-party vote total")
        return 100.0 * (float(self.dem_votes) - float(self.rep_votes)) / total


@dataclass(frozen=True)
class StateHistory:
    """State + matching national presidential results for the lean-weight years."""

    state: str
    state_results: dict[int, PresidentialResult]
    national_results: dict[int, PresidentialResult]


@dataclass(frozen=True)
class CandidateInfo:
    name: str
    party: Party
    is_incumbent: bool = False
    status: str = "confirmed"  # confirmed / presumptive / unconfirmed / withdrawn
    source: Optional[str] = None


@dataclass(frozen=True)
class RaceMeta:
    """Canonical race identity (spec section 7)."""

    race_id: str  # e.g. "nc-sen-2026"
    state: str  # two-letter, upper
    office: Office
    cycle: int
    election_date: date
    dem_candidate: Optional[CandidateInfo] = None
    rep_candidate: Optional[CandidateInfo] = None

    @property
    def incumbent_party(self) -> Optional[Party]:
        for c in (self.dem_candidate, self.rep_candidate):
            if c and c.is_incumbent:
                return c.party
        return None

    @property
    def nominees_confirmed(self) -> bool:
        return bool(
            self.dem_candidate
            and self.rep_candidate
            and self.dem_candidate.status in {"confirmed", "presumptive"}
            and self.rep_candidate.status in {"confirmed", "presumptive"}
        )


@dataclass(frozen=True)
class QuantForecastInput:
    """The complete, market-free input to :func:`app.quant.engine.run_quant_forecast`."""

    race: RaceMeta
    as_of: datetime
    polls: tuple[NormalizedPoll, ...] = ()
    generic_ballot: tuple[GenericBallotPoll, ...] = ()
    state_history: Optional[StateHistory] = None
    # Direct national-environment override (Dem points), used when generic_ballot is empty but a
    # National-environment provider supplied a computed value. Never a market-derived number.
    national_environment_override: Optional[float] = None
    national_environment_stale: bool = False
    candidate_mapping_confidence: float = 1.0
    provider_degraded: bool = False
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        assert_market_free(
            {
                "national_environment_override": self.national_environment_override,
                "notes": list(self.notes),
            }
        )

    @property
    def as_of_date(self) -> date:
        return self.as_of.date()

    @property
    def days_to_election(self) -> float:
        return float((self.race.election_date - self.as_of_date).days)

    def input_hash(self) -> str:
        return _stable_hash(
            {
                "race_id": self.race.race_id,
                "state": self.race.state,
                "office": self.race.office,
                "cycle": self.race.cycle,
                "election_date": self.race.election_date,
                "as_of": self.as_of,
                "incumbent_party": self.race.incumbent_party,
                "dem_candidate": _cand(self.race.dem_candidate),
                "rep_candidate": _cand(self.race.rep_candidate),
                "polls": [
                    {
                        "pollster": p.pollster,
                        "end_date": p.end_date,
                        "dem_pct": p.dem_pct,
                        "rep_pct": p.rep_pct,
                        "sample_size": p.sample_size,
                        "population": p.population,
                        "pollster_grade": p.pollster_grade,
                        "partisan_sponsor": p.partisan_sponsor,
                        "internal": p.internal,
                        "poll_id": p.poll_id,
                    }
                    for p in sorted(self.polls, key=lambda x: (x.end_date, x.pollster, x.poll_id or ""))
                ],
                "generic_ballot": [
                    {
                        "pollster": g.pollster,
                        "end_date": g.end_date,
                        "dem_pct": g.dem_pct,
                        "rep_pct": g.rep_pct,
                        "sample_size": g.sample_size,
                        "population": g.population,
                        "pollster_grade": g.pollster_grade,
                    }
                    for g in sorted(self.generic_ballot, key=lambda x: (x.end_date, x.pollster))
                ],
                "state_history": _history(self.state_history),
                "national_environment_override": self.national_environment_override,
                "national_environment_stale": self.national_environment_stale,
                "candidate_mapping_confidence": self.candidate_mapping_confidence,
                "provider_degraded": self.provider_degraded,
                "methodology": "input-v1",
            }
        )


def _cand(c: Optional[CandidateInfo]) -> Optional[dict]:
    if c is None:
        return None
    return {"name": c.name, "party": c.party, "is_incumbent": c.is_incumbent, "status": c.status}


def _history(h: Optional[StateHistory]) -> Optional[dict]:
    if h is None:
        return None
    return {
        "state": h.state,
        "state_results": {y: r.margin for y, r in h.state_results.items()},
        "national_results": {y: r.margin for y, r in h.national_results.items()},
    }


# --------------------------------------------------------------------------------------------------
# Intermediate + final results
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PollingAverage:
    polling_margin: Optional[float]
    n_eff: float
    raw_poll_count: int
    used_poll_count: int
    latest_poll_date: Optional[date]
    average_poll_age_days: Optional[float]
    pollster_diversity: int
    sum_weights: float
    per_poll: tuple[dict[str, Any], ...] = ()  # transparency: one entry per poll with its weight


@dataclass(frozen=True)
class Fundamentals:
    fundamental_margin: Optional[float]
    state_lean: Optional[float]
    national_environment: Optional[float]
    incumbency_adjustment: float
    incumbent_party: Optional[str]
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UncertaintyBreakdown:
    sigma_total: float
    sigma_time: float
    sigma_polling: float
    sigma_office: float
    sigma_status: float


@dataclass(frozen=True)
class QuantForecastResult:
    race_id: str
    methodology_version: str
    config_hash: str
    input_hash: str
    generated_at: datetime

    data_quality: str  # STRONG / NORMAL / THIN / DEGRADED / ABSTAIN
    abstained: bool
    abstain_reasons: tuple[str, ...]

    polling_margin: Optional[float]
    fundamental_margin: Optional[float]
    poll_weight: float  # alpha
    expected_margin: Optional[float]  # mu

    uncertainty: Optional[UncertaintyBreakdown]

    p_dem_win: Optional[float]  # capped, published
    p_rep_win: Optional[float]
    p_dem_win_uncapped: Optional[float]

    polling: Optional[PollingAverage]
    fundamentals: Optional[Fundamentals]
    detail: dict[str, Any] = field(default_factory=dict)

    def as_public_dict(self) -> dict[str, Any]:
        """Shape used by the export bundle and the market-detail page (spec section 37)."""
        u = self.uncertainty
        return {
            "race_id": self.race_id,
            "methodology_version": self.methodology_version,
            "config_hash": self.config_hash,
            "input_hash": self.input_hash,
            "generated_at": self.generated_at.isoformat(),
            "data_quality": self.data_quality,
            "abstained": self.abstained,
            "abstain_reasons": list(self.abstain_reasons),
            "polling_margin": self.polling_margin,
            "fundamental_margin": self.fundamental_margin,
            "poll_weight": self.poll_weight,
            "expected_margin": self.expected_margin,
            "sigma_total": None if u is None else u.sigma_total,
            "sigma_components": None
            if u is None
            else {
                "time": u.sigma_time,
                "polling": u.sigma_polling,
                "office": u.sigma_office,
                "status": u.sigma_status,
            },
            "p_dem_win": self.p_dem_win,
            "p_rep_win": self.p_rep_win,
            "p_dem_win_uncapped": self.p_dem_win_uncapped,
        }


@dataclass(frozen=True)
class EnsembleResult:
    available: bool
    ensemble_probability: Optional[float]
    weights: dict[str, float]
    components: dict[str, Optional[float]]  # quant / openai / anthropic
    dispersion: Optional[float]
    max_pairwise_disagreement: Optional[float]
    robustness: Optional[str]  # HIGH / MEDIUM / LOW / None
    unavailable_reason: Optional[str] = None


@dataclass(frozen=True)
class EvidenceBundle:
    """Immutable, timestamp-locked, market-free snapshot of a forecast's inputs (spec section 22)."""

    race_id: str
    forecast_timestamp: datetime
    election_date: date
    payload: dict[str, Any]
    content_hash: str

    @staticmethod
    def build(race_id: str, forecast_timestamp: datetime, election_date: date, payload: dict) -> EvidenceBundle:
        assert_market_free(payload, path="evidence_bundle")
        body = {
            "race_id": race_id,
            "forecast_timestamp": forecast_timestamp,
            "election_date": election_date,
            **payload,
        }
        return EvidenceBundle(
            race_id=race_id,
            forecast_timestamp=forecast_timestamp,
            election_date=election_date,
            payload=body,
            content_hash=_stable_hash(body),
        )


def iter_forbidden_hits(obj: Any) -> Iterable[str]:
    """Diagnostic helper: yield the dotted paths at which forbidden keys appear (for tests)."""

    def _walk(o: Any, path: str):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(k, str) and k.strip().lower() in FORBIDDEN_INPUT_KEYS:
                    yield f"{path}.{k}"
                yield from _walk(v, f"{path}.{k}")
        elif isinstance(o, (list, tuple)):
            for i, v in enumerate(o):
                yield from _walk(v, f"{path}[{i}]")

    yield from _walk(obj, "root")
