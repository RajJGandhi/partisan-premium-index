"""PPI v1.5 ensemble wiring (spec sections 25, 27).

Joins a persisted Quant forecast with the persisted GPT + Claude blind benchmarks and writes an
``ensemble_forecasts`` row:

    PPI_ensemble = 0.60*Quant + 0.20*GPT + 0.20*Claude          (predeclared, never re-fit)

If any component probability is missing (provider skipped, failed, or **abstained**) the row is
recorded ``available = False`` with a reason -- the present components are never reweighted. The
robustness band needs a market probability and is computed only here, strictly after every
forecast has been persisted (spec section 18).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blind.runner import STATUS_OK
from app.config import get_settings
from app.db.models_quant import BlindBenchmarkForecast, EnsembleForecast, QuantForecast
from app.quant.config import QUANT_V1, MethodologyConfig
from app.quant.ensemble import combine_ensemble


def _quant_probability(q: QuantForecast) -> Optional[float]:
    if q.abstained:
        return None
    if q.fair_value_yes is not None:
        return float(q.fair_value_yes)
    return None if q.p_dem_win is None else float(q.p_dem_win)


def _usable_probability(row: Optional[BlindBenchmarkForecast]) -> Optional[float]:
    if row is None or row.status != STATUS_OK or row.probability is None:
        return None
    return float(row.probability)


def _newest_ensemble(
    session: Session, *, race_id: str, run_key: str, methodology_version: str
) -> Optional[EnsembleForecast]:
    return session.execute(
        select(EnsembleForecast)
        .where(
            EnsembleForecast.race_id == race_id,
            EnsembleForecast.run_key == run_key,
            EnsembleForecast.methodology_version == methodology_version,
        )
        .order_by(EnsembleForecast.revision.desc())
        .limit(1)
    ).scalar_one_or_none()


def compute_and_persist_ensemble(
    session: Session,
    *,
    race_id: str,
    run_key: str,
    quant_forecast: QuantForecast,
    blind_rows: Sequence[BlindBenchmarkForecast],
    market_probability: float | None = None,
    methodology_version: str | None = None,
    job_run_id: int | None = None,
    cfg: MethodologyConfig = QUANT_V1,
) -> tuple[EnsembleForecast, bool]:
    """Return ``(row, created)``. ``created`` is False when an identical slot row already exists."""
    methodology_version = methodology_version or get_settings().ensemble_methodology_version

    openai_row = next((r for r in blind_rows if r.provider == "openai"), None)
    anthropic_row = next((r for r in blind_rows if r.provider == "anthropic"), None)

    quant_p = _quant_probability(quant_forecast)
    openai_p = _usable_probability(openai_row)
    anthropic_p = _usable_probability(anthropic_row)

    result = combine_ensemble(
        quant=quant_p,
        openai=openai_p,
        anthropic=anthropic_p,
        market_probability=market_probability,
        cfg=cfg,
    )

    is_stub = any(
        getattr(r, "publication_status", "") == "STUB" for r in (openai_row, anthropic_row) if r is not None
    )

    prev = _newest_ensemble(
        session, race_id=race_id, run_key=run_key, methodology_version=methodology_version
    )
    if prev is not None:
        same = (
            prev.available == result.available
            and prev.quant_probability == quant_p
            and prev.openai_probability == openai_p
            and prev.anthropic_probability == anthropic_p
        )
        if same:
            return prev, False
    revision = 0 if prev is None else prev.revision + 1

    row = EnsembleForecast(
        race_id=race_id,
        job_run_id=job_run_id,
        run_key=run_key,
        methodology_version=methodology_version,
        generated_at=datetime.now(timezone.utc),
        available=result.available,
        unavailable_reason=result.unavailable_reason,
        quant_forecast_id=quant_forecast.id,
        quant_probability=quant_p,
        openai_forecast_id=openai_row.id if openai_row is not None else None,
        openai_probability=openai_p,
        anthropic_forecast_id=anthropic_row.id if anthropic_row is not None else None,
        anthropic_probability=anthropic_p,
        weights_json=json.dumps(result.weights),
        ensemble_probability=result.ensemble_probability,
        dispersion=result.dispersion,
        max_pairwise_disagreement=result.max_pairwise_disagreement,
        robustness=result.robustness,
        pipeline_mode="shadow",
        publication_status="STUB" if is_stub else "SHADOW",
        revision=revision,
        correction_of_id=prev.id if prev is not None else None,
    )
    session.add(row)
    session.flush()
    return row, True
