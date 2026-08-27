"""Stage 9 -- join persisted forecasts with the market snapshot (spec sections 18, 19).

Runs strictly **after** every forecast for a race has been persisted. Reads the latest
``MarketSnapshot`` for the race's linked Polymarket contract, orients it to
``P(contract_yes_party wins)`` via ``race.market_yes_party``, and writes one
``forecast_market_comparisons`` row per forecasting series:

    market_model_spread = MarketProbability - PPIFairValue

This is the canonical observed quantity. It is an observation, **not** proof of partisan bias.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MarketSnapshot
from app.db.models_quant import (
    EnsembleForecast,
    ForecastMarketComparison,
    QuantForecast,
    Race,
)
from app.eval.series import orient_probability
from app.quant.config import QUANT_V1, MethodologyConfig
from app.quant.ensemble import robustness_band


def _market_probability(snap: MarketSnapshot, yes_party: str | None, contract_yes: str) -> tuple[float | None, str | None]:
    raw = snap.comparison_price if snap.comparison_price is not None else snap.yes_midpoint
    quote_method = "midpoint"
    if raw is None:
        raw = snap.last_trade_price
        quote_method = "last_trade"
    return orient_probability(raw, yes_party, contract_yes), (quote_method if raw is not None else None)


def join_forecasts_with_market(
    session: Session,
    *,
    race_id: str,
    run_key: str,
    cfg: MethodologyConfig = QUANT_V1,
) -> list[ForecastMarketComparison]:
    """Write ``forecast_market_comparisons`` rows for this race/run. Returns them (possibly empty)."""
    race = session.execute(select(Race).where(Race.race_id == race_id)).scalar_one_or_none()
    if race is None or race.polymarket_market_id is None or not race.market_yes_party:
        return []

    snap = session.execute(
        select(MarketSnapshot)
        .where(MarketSnapshot.market_id == race.polymarket_market_id)
        .order_by(MarketSnapshot.timestamp.desc())
        .limit(1)
    ).scalar_one_or_none()
    if snap is None:
        return []

    contract_yes = (race.contract_yes_party or "DEM").upper()
    market_p, quote_method = _market_probability(snap, race.market_yes_party, contract_yes)
    if market_p is None:
        return []

    quant = session.execute(
        select(QuantForecast)
        .where(QuantForecast.race_id == race_id, QuantForecast.run_key == run_key,
               QuantForecast.abstained.is_(False))
        .order_by(QuantForecast.revision.desc())
        .limit(1)
    ).scalar_one_or_none()
    ensemble = session.execute(
        select(EnsembleForecast)
        .where(EnsembleForecast.race_id == race_id, EnsembleForecast.run_key == run_key,
               EnsembleForecast.available.is_(True))
        .order_by(EnsembleForecast.revision.desc())
        .limit(1)
    ).scalar_one_or_none()

    # robustness needs the three model probabilities + the market gap; only defined once ensemble exists
    robustness = None
    if ensemble is not None and ensemble.ensemble_probability is not None:
        vals = [v for v in (ensemble.quant_probability, ensemble.openai_probability, ensemble.anthropic_probability)
                if v is not None]
        max_pair = max((abs(a - b) for i, a in enumerate(vals) for b in vals[i + 1:]), default=0.0)
        robustness = robustness_band(
            market_probability=market_p,
            ensemble_probability=ensemble.ensemble_probability,
            max_pairwise=max_pair,
            cfg=cfg,
        )

    written: list[ForecastMarketComparison] = []
    plan: list[tuple[str, float | None, int | None, int | None]] = []
    if quant is not None:
        qp = quant.fair_value_yes if quant.fair_value_yes is not None else (
            quant.p_dem_win if contract_yes == "DEM" else (None if quant.p_dem_win is None else 1.0 - quant.p_dem_win)
        )
        plan.append(("quant", qp, quant.id, None))
    if ensemble is not None and ensemble.ensemble_probability is not None:
        ep = ensemble.ensemble_probability if contract_yes == "DEM" else 1.0 - ensemble.ensemble_probability
        plan.append(("ensemble", ep, None, ensemble.id))

    for series, fair_value, qf_id, ens_id in plan:
        if fair_value is None:
            continue
        existing = session.execute(
            select(ForecastMarketComparison).where(
                ForecastMarketComparison.race_id == race_id,
                ForecastMarketComparison.run_key == run_key,
                ForecastMarketComparison.series == series,
                ForecastMarketComparison.market_snapshot_id == snap.id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            written.append(existing)
            continue
        spread = market_p - fair_value
        row = ForecastMarketComparison(
            race_id=race_id,
            market_id=race.polymarket_market_id,
            run_key=run_key,
            series=series,
            quant_forecast_id=qf_id,
            ensemble_forecast_id=ens_id,
            market_snapshot_id=snap.id,
            observed_at=datetime.now(timezone.utc),
            fair_value=fair_value,
            market_probability=market_p,
            quote_method=quote_method,
            market_model_spread=spread,
            abs_spread=abs(spread),
            robustness=robustness if series == "ensemble" else None,
            liquidity=snap.liquidity,
            volume=snap.volume,
        )
        session.add(row)
        session.flush()
        written.append(row)
    return written
