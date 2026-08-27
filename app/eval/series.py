"""Collect the per-series forecast observation history for a race (spec sections 34, 36).

Each forecasting series is reduced to a time-ordered list of :class:`Observation` -- one point per
scheduled run -- so scoring can pick the point nearest a horizon and lead/lag analysis can compare
two series over time. Every probability is oriented to ``P(contract_yes_party wins)`` (default
``DEM``): Quant ``p_dem_win``, the blind prompt, and the resolution are already in that space; the
``market`` and ``legacy_llm`` series are flipped using ``race.market_yes_party`` and are dropped
entirely when that is unknown (abstain rather than guess a direction).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LLMForecast, Market, MarketSnapshot
from app.db.models_quant import (
    BlindBenchmarkForecast,
    EnsembleForecast,
    QuantForecast,
    Race,
)

SERIES_QUANT = "quant"
SERIES_OPENAI = "openai"
SERIES_ANTHROPIC = "anthropic"
SERIES_ENSEMBLE = "ensemble"
SERIES_MARKET = "market"
SERIES_LEGACY_LLM = "legacy_llm"

ALL_SERIES = (
    SERIES_MARKET,
    SERIES_QUANT,
    SERIES_OPENAI,
    SERIES_ANTHROPIC,
    SERIES_ENSEMBLE,
    SERIES_LEGACY_LLM,
)


@dataclass(frozen=True)
class Observation:
    series: str
    observed_at: datetime
    probability: float  # P(contract_yes_party wins), [0, 1]
    methodology_version: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


def _aware(dt: datetime) -> datetime:
    """Coerce a stored timestamp to timezone-aware UTC (the forecast tables are all NOT NULL)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def orient_probability(p: float | None, yes_party: str | None, contract_yes_party: str) -> Optional[float]:
    """Flip ``p`` if the source's YES side names the other party. ``None`` if it can't be oriented."""
    if p is None or yes_party is None:
        return None
    yes_party = yes_party.upper()
    contract_yes_party = contract_yes_party.upper()
    if yes_party == contract_yes_party:
        return float(p)
    if {yes_party, contract_yes_party} == {"DEM", "REP"}:
        return 1.0 - float(p)
    return None


def collect_series(session: Session, race_id: str) -> dict[str, list[Observation]]:
    race = session.execute(select(Race).where(Race.race_id == race_id)).scalar_one_or_none()
    if race is None:
        return {}
    contract_yes = (race.contract_yes_party or "DEM").upper()
    out: dict[str, list[Observation]] = {s: [] for s in ALL_SERIES}

    # --- quant ---------------------------------------------------------------------------------
    for q in session.execute(
        select(QuantForecast).where(QuantForecast.race_id == race_id, QuantForecast.abstained.is_(False))
    ).scalars():
        p = q.fair_value_yes if q.fair_value_yes is not None else q.p_dem_win
        if p is None:
            continue
        # p_dem_win is P(DEM wins); flip if the contract's YES is REP
        prob = float(p) if contract_yes == "DEM" else 1.0 - float(p)
        out[SERIES_QUANT].append(
            Observation(SERIES_QUANT, _aware(q.generated_at), prob, q.methodology_version,
                        {"run_key": q.run_key, "revision": q.revision})
        )

    # --- blind benchmarks (probability is already P(DEM wins) from the blind prompt) ----------
    for b in session.execute(
        select(BlindBenchmarkForecast).where(
            BlindBenchmarkForecast.race_id == race_id, BlindBenchmarkForecast.status == "OK"
        )
    ).scalars():
        if b.probability is None:
            continue
        prob = float(b.probability) if contract_yes == "DEM" else 1.0 - float(b.probability)
        series = SERIES_OPENAI if b.provider == "openai" else SERIES_ANTHROPIC if b.provider == "anthropic" else None
        if series is None:
            continue
        out[series].append(
            Observation(series, _aware(b.generated_at), prob, b.methodology_version,
                        {"run_key": b.run_key, "model": b.model_name})
        )

    # --- ensemble -----------------------------------------------------------------------------
    for e in session.execute(
        select(EnsembleForecast).where(
            EnsembleForecast.race_id == race_id, EnsembleForecast.available.is_(True)
        )
    ).scalars():
        if e.ensemble_probability is None:
            continue
        prob = float(e.ensemble_probability) if contract_yes == "DEM" else 1.0 - float(e.ensemble_probability)
        out[SERIES_ENSEMBLE].append(
            Observation(SERIES_ENSEMBLE, _aware(e.generated_at), prob, e.methodology_version,
                        {"run_key": e.run_key})
        )

    # --- market + legacy LLM (need the linked Polymarket contract's YES orientation) ----------
    if race.polymarket_market_id is not None and race.market_yes_party:
        market = session.get(Market, race.polymarket_market_id)
        for snap in session.execute(
            select(MarketSnapshot).where(MarketSnapshot.market_id == race.polymarket_market_id)
        ).scalars():
            raw = snap.comparison_price if snap.comparison_price is not None else snap.yes_midpoint
            oriented = orient_probability(raw, race.market_yes_party, contract_yes)
            if oriented is None:
                continue
            out[SERIES_MARKET].append(
                Observation(SERIES_MARKET, _aware(snap.timestamp), oriented, None,
                            {"quote_method": snap.price_type, "liquidity": snap.liquidity,
                             "volume": snap.volume})
            )
        for lf in session.execute(
            select(LLMForecast).where(
                LLMForecast.market_id == race.polymarket_market_id,
                LLMForecast.forecast_role == "legacy_blind_llm",
                LLMForecast.status == "OK",
            )
        ).scalars():
            oriented = orient_probability(lf.fair_value, race.market_yes_party, contract_yes)
            if oriented is None:
                continue
            out[SERIES_LEGACY_LLM].append(
                Observation(SERIES_LEGACY_LLM, _aware(lf.generated_at), oriented, lf.methodology_version,
                            {"run_slot": lf.run_slot, "model": lf.model_name})
            )
        _ = market  # kept for future contract-metadata use

    for s in out:
        out[s].sort(key=lambda o: o.observed_at)
    return out
