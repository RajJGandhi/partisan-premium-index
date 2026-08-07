"""Read-side helpers for presenting the primary blind-LLM (Qwen) forecast history.

Kept separate from the Streamlit page itself so the presentation logic -- freshness
classification, the derived confidence band, and the row/export shape -- is unit-testable
without driving a Streamlit script. Nothing here writes to the database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EvidenceItem, LLMForecast, Market

FRESHNESS_MISSING = "MISSING"
FRESHNESS_OK = "OK"
FRESHNESS_STALE = "STALE"
FRESHNESS_ERROR = "ERROR"
FRESHNESS_SKIPPED = "SKIPPED_PROVIDER"

DEFAULT_STALE_HOURS = 30.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def classify_forecast_freshness(
    latest: LLMForecast | None, *, now: datetime | None = None, stale_hours: float = DEFAULT_STALE_HOURS
) -> str:
    """Classify a market's most recent forecast slot for the stale/error banner.

    MISSING: no forecast has ever been generated for this market.
    ERROR: the most recent attempt failed to produce a usable forecast.
    SKIPPED_PROVIDER: the most recent attempt wasn't run at all (e.g. LLM_PROVIDER=deterministic).
    STALE: the most recent usable forecast is older than `stale_hours`.
    OK: a usable forecast exists and is recent.
    """
    if latest is None:
        return FRESHNESS_MISSING
    if latest.status == "FAILED":
        return FRESHNESS_ERROR
    if latest.status == "SKIPPED_PROVIDER":
        return FRESHNESS_SKIPPED
    now = now or utcnow()
    age_hours = (now - _aware(latest.generated_at)).total_seconds() / 3600
    return FRESHNESS_STALE if age_hours > stale_hours else FRESHNESS_OK


def approx_confidence_interval(fair_value: float, confidence: float) -> tuple[float, float]:
    """A heuristic uncertainty band, *not* a statistical confidence interval.

    The model reports a single self-assessed confidence score in [0, 1], not interval bounds.
    This widens symmetrically around fair_value as confidence falls, purely so the UI can show
    an at-a-glance range. Callers must label this as approximate/derived, never as a native
    model output.
    """
    half_width = max(0.0, min(1.0, 1.0 - confidence)) * 0.5
    lower = max(0.0, fair_value - half_width)
    upper = min(1.0, fair_value + half_width)
    return lower, upper


def latest_forecast_by_market(forecasts: list[LLMForecast]) -> dict[int, LLMForecast]:
    latest: dict[int, LLMForecast] = {}
    for f in forecasts:
        current = latest.get(f.market_id)
        if current is None or _aware(f.generated_at) > _aware(current.generated_at):
            latest[f.market_id] = f
    return latest


def evidence_items_for_forecast(session: Session, forecast: LLMForecast) -> list[EvidenceItem]:
    try:
        ids = json.loads(forecast.evidence_ids_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    if not ids:
        return []
    rows = list(session.scalars(select(EvidenceItem).where(EvidenceItem.id.in_(ids))))
    by_id = {r.id: r for r in rows}
    return [by_id[i] for i in ids if i in by_id]


@dataclass(frozen=True)
class ForecastRow:
    market_id: int
    market_label: str
    run_slot: str
    generated_at: datetime
    generation_status: str
    reviewed_status: str
    fair_value: float | None
    confidence: float | None
    interval_low: float | None
    interval_high: float | None
    should_abstain: bool | None
    model_name: str
    prompt_version: str
    retries: int
    error_message: str | None
    comparison_price_at_join: float | None
    raw_ppi: float | None
    joined_at: datetime | None
    rationale: str | None
    base_rate_notes: str | None
    key_uncertainties: list[str]


def build_forecast_rows(forecasts: list[LLMForecast], markets_by_id: dict[int, Market]) -> list[ForecastRow]:
    rows = []
    for f in forecasts:
        market = markets_by_id.get(f.market_id)
        label = f"{market.tracking_id or market.id} · {market.question}" if market else f"market_id={f.market_id}"
        try:
            key_uncertainties = json.loads(f.key_uncertainties_json or "[]")
        except (json.JSONDecodeError, TypeError):
            key_uncertainties = []
        interval_low = interval_high = None
        if f.fair_value is not None and f.confidence is not None:
            interval_low, interval_high = approx_confidence_interval(f.fair_value, f.confidence)
        rows.append(
            ForecastRow(
                market_id=f.market_id,
                market_label=label,
                run_slot=f.run_slot,
                generated_at=f.generated_at,
                generation_status=f.status,
                reviewed_status=f.reviewed_status,
                fair_value=f.fair_value,
                confidence=f.confidence,
                interval_low=interval_low,
                interval_high=interval_high,
                should_abstain=f.should_abstain,
                model_name=f.model_name,
                prompt_version=f.prompt_version,
                retries=f.retries,
                error_message=f.error_message,
                # Price/PPI-gap fields are populated by join_forecast_with_price only after the
                # forecast row is already committed; a row that hasn't been joined yet simply
                # carries None here, so the UI never shows a price for an unjoined forecast.
                comparison_price_at_join=f.comparison_price_at_join,
                raw_ppi=f.raw_ppi,
                joined_at=f.joined_at,
                rationale=f.rationale,
                base_rate_notes=f.base_rate_notes,
                key_uncertainties=key_uncertainties,
            )
        )
    return rows


EXPORT_COLUMNS = [
    "market_label",
    "run_slot",
    "generated_at",
    "generation_status",
    "reviewed_status",
    "fair_value",
    "confidence",
    "interval_low",
    "interval_high",
    "should_abstain",
    "model_name",
    "prompt_version",
    "retries",
    "error_message",
    "comparison_price_at_join",
    "raw_ppi",
    "joined_at",
    "rationale",
    "base_rate_notes",
]


def forecast_rows_to_export_dataframe(rows: list[ForecastRow]) -> pd.DataFrame:
    """Plain, unformatted columns suitable for CSV export -- no percent strings, no styling."""
    return pd.DataFrame([{col: getattr(r, col) for col in EXPORT_COLUMNS} for r in rows], columns=EXPORT_COLUMNS)
