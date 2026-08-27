"""Headline-series cutover mechanism (spec sections 25, 50 Phase E).

PPI v1 keeps the legacy blind-LLM series as the public headline **until the v1.5 pipeline passes
the validation checklist in ``docs/research/PPI_CUTOVER.md``**. This module is the switch, not the
decision: ``headline_series()`` reads ``PPI_HEADLINE_SERIES`` (default ``legacy_blind_llm``) and
``headline_forecast()`` returns the current public fair value for a race from whichever series is
configured. Flipping the headline is a one-line env change once the checklist is signed off.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models_quant import EnsembleForecast, ForecastResolution, QuantForecast

LEGACY = "legacy_blind_llm"
QUANT = "quant"
ENSEMBLE = "ensemble"
VALID_HEADLINE_SERIES = (LEGACY, QUANT, ENSEMBLE)


def headline_series() -> str:
    s = (get_settings().ppi_headline_series or LEGACY).strip().lower()
    return s if s in VALID_HEADLINE_SERIES else LEGACY


@dataclass(frozen=True)
class HeadlineForecast:
    race_id: str
    series: str
    probability: Optional[float]
    methodology_version: Optional[str]
    run_key: Optional[str]
    is_shadow: bool


def headline_forecast(session: Session, race_id: str) -> Optional[HeadlineForecast]:
    """Return the current public headline fair value for a race, per ``PPI_HEADLINE_SERIES``.

    ``None`` when the configured series has no usable forecast for the race. For ``legacy_blind_llm``
    this module returns ``None`` -- the legacy headline is served by the existing
    ``app.ppi.public_forecast`` path, unchanged; this only handles the v1.5 series.
    """
    series = headline_series()
    if series == LEGACY:
        return None
    if series == QUANT:
        qrow = session.execute(
            select(QuantForecast)
            .where(QuantForecast.race_id == race_id, QuantForecast.abstained.is_(False))
            .order_by(QuantForecast.generated_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if qrow is None:
            return None
        p = qrow.fair_value_yes if qrow.fair_value_yes is not None else qrow.p_dem_win
        return HeadlineForecast(race_id, QUANT, p, qrow.methodology_version, qrow.run_key,
                                qrow.publication_status != "CANONICAL")
    erow = session.execute(
        select(EnsembleForecast)
        .where(EnsembleForecast.race_id == race_id, EnsembleForecast.available.is_(True))
        .order_by(EnsembleForecast.generated_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if erow is None:
        return None
    return HeadlineForecast(race_id, ENSEMBLE, erow.ensemble_probability, erow.methodology_version,
                            erow.run_key, erow.publication_status != "CANONICAL")


# --- validation checklist (spec section 50 Phase D/E) ------------------------------------------
CUTOVER_CHECKLIST = (
    "the v1.5 pipeline has run twice daily for >= 30 canonical cycles with no unexplained failures",
    "quant + ensemble forecasts exist for every enabled supported race on every cycle",
    "the strong-lead / toss-up / symmetry / monotonicity invariant tests pass on the live config",
    "market_model_spread has been recorded (forecast_market_comparisons) for >= 2 weeks",
    "at least one resolved race has been scored for every series without a point-in-time leak",
    "provider_health shows no series stuck DEGRADED/DOWN for the acquisition path Quant depends on",
    "a dated decision record in docs/research/PPI_CUTOVER.md selects Quant or Ensemble as headline",
)


def cutover_readiness(session: Session) -> dict:
    """A best-effort, non-authoritative readiness snapshot for the System Status page."""
    n_quant = session.execute(select(QuantForecast).where(QuantForecast.abstained.is_(False))).scalars().all()
    n_ens = session.execute(select(EnsembleForecast).where(EnsembleForecast.available.is_(True))).scalars().all()
    n_resolved = session.execute(select(ForecastResolution.race_id)).scalars().all()
    return {
        "current_headline_series": headline_series(),
        "checklist": list(CUTOVER_CHECKLIST),
        "quant_forecasts": len(n_quant),
        "available_ensembles": len(n_ens),
        "resolved_races": len(n_resolved),
        "note": "advisory only -- the flip is a manual, dated decision (docs/research/PPI_CUTOVER.md)",
    }
