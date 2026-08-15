"""Public-visibility derivation for the primary blind-LLM (Qwen) forecast series.

A canonical forecast becomes publicly visible automatically once persisted -- there is no human
approval gate on publication (see CLAUDE.md's research-integrity rules: "a valid model
probability is final for that run"). Human review can still flag a forecast for a genuine
data-integrity concern (``LLMForecast.reviewed_status == "FLAGGED"``, see
``app.ppi.llm_forecast_review``), which removes it from public display without editing or
fabricating anything -- it is never used to selectively approve forecasts based on their content.

"Current" means the most recent forecast, for a given market, belonging to a canonical and
non-superseded run -- the same canonical-and-current definition used elsewhere (see
``app.ppi.run_classification``), applied per market instead of per job run.

Nothing here writes to the database, and nothing here ever touches ``DATABASE_URL`` or any other
secret -- only forecast fields that are already safe for public display.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobRun, LLMForecast
from app.ppi.blind_forecast import PRIMARY_SERIES_PROVIDERS

# NONE: no canonical forecast has ever been generated for this market.
# OK: a live probability was produced and is shown.
# ABSTAINED: the model was asked and declined to give a probability -- shown as an explicit
#   abstention, never a fabricated value.
# ERROR: the most recent canonical attempt failed to produce a usable forecast (FAILED /
#   SKIPPED_PROVIDER / any unexpected status) -- shown as unavailable, never a fallback value.
# FLAGGED: a human reviewer flagged a genuine data-integrity concern -- suppressed from display.
PUBLIC_FORECAST_STATUSES = ("OK", "ABSTAINED", "ERROR", "FLAGGED", "NONE")

_VALUE_BEARING_STATUSES = {"OK"}
_CONTEXT_BEARING_STATUSES = {"OK", "ABSTAINED"}


@dataclass(frozen=True)
class PublicForecast:
    market_id: int
    forecast_status: str
    fair_value: float | None
    market_probability: float | None
    partisan_premium: float | None
    confidence: float | None
    rationale: str | None
    generated_at: datetime | None
    run_key: str | None
    job_run_id: int | None
    model_name: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "forecast_status": self.forecast_status,
            "fair_value": self.fair_value,
            "market_probability": self.market_probability,
            "partisan_premium": self.partisan_premium,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "run_key": self.run_key,
            "job_run_id": self.job_run_id,
            "model_name": self.model_name,
        }


def _to_public_forecast(market_id: int, forecast: LLMForecast | None) -> PublicForecast:
    if forecast is None:
        return PublicForecast(
            market_id=market_id,
            forecast_status="NONE",
            fair_value=None,
            market_probability=None,
            partisan_premium=None,
            confidence=None,
            rationale=None,
            generated_at=None,
            run_key=None,
            job_run_id=None,
            model_name=None,
        )

    if forecast.reviewed_status == "FLAGGED":
        status = "FLAGGED"
    elif forecast.status == "OK":
        status = "OK"
    elif forecast.status == "ABSTAINED":
        status = "ABSTAINED"
    else:
        status = "ERROR"

    show_values = status in _VALUE_BEARING_STATUSES
    show_context = status in _CONTEXT_BEARING_STATUSES
    return PublicForecast(
        market_id=market_id,
        forecast_status=status,
        # fair_value/market_probability/partisan_premium come from the fields generate_blind_forecast
        # and join_forecast_with_price already persisted immutably at generation/join time -- never
        # recomputed against a fresher price, so the displayed spread always equals what was actually
        # observed then, not a moving target.
        fair_value=forecast.fair_value if show_values else None,
        market_probability=forecast.comparison_price_at_join if show_values else None,
        partisan_premium=forecast.raw_ppi if show_values else None,
        confidence=forecast.confidence if show_context else None,
        rationale=forecast.rationale if show_context else None,
        generated_at=forecast.generated_at,
        run_key=forecast.run_key,
        job_run_id=forecast.job_run_id,
        model_name=forecast.model_name,
    )


def current_public_forecasts(session: Session, market_ids: list[int]) -> dict[int, PublicForecast]:
    """One ``PublicForecast`` per requested market_id, always -- markets with no canonical
    forecast yet get an explicit ``forecast_status="NONE"`` entry rather than being omitted."""
    if not market_ids:
        return {}

    query = (
        select(LLMForecast)
        .join(JobRun, LLMForecast.job_run_id == JobRun.id)
        .where(
            LLMForecast.market_id.in_(market_ids),
            # Scoped to the primary (headline) series only. Without this, an experimental
            # comparison series' row (e.g. openrouter/DeepSeek) sharing a market/cycle could have
            # a later generated_at than the primary series' own row and would otherwise silently
            # become "the" public forecast -- exactly what must never happen (see
            # docs/research/PPI_DEEPSEEK_VS_QWEN_PREREGISTRATION.md Section 17).
            LLMForecast.model_provider.in_(PRIMARY_SERIES_PROVIDERS),
            JobRun.run_classification == "canonical",
            JobRun.superseded_by_id.is_(None),
        )
        .order_by(LLMForecast.market_id, LLMForecast.generated_at.desc())
    )
    latest_by_market: dict[int, LLMForecast] = {}
    for forecast in session.scalars(query):
        # Rows arrive ordered by generated_at desc within each market_id, so the first one seen
        # per market is the latest -- never let a later (older) row overwrite it.
        latest_by_market.setdefault(forecast.market_id, forecast)

    return {
        market_id: _to_public_forecast(market_id, latest_by_market.get(market_id)) for market_id in market_ids
    }


def current_public_forecast(session: Session, market_id: int) -> PublicForecast:
    return current_public_forecasts(session, [market_id])[market_id]
