"""Data-integrity review layer for the primary blind-LLM (Qwen) forecast series.

A generated ``LLMForecast`` row is immutable: ``app.ppi.blind_forecast.generate_blind_forecast``
never edits a slot already at status ``OK``. A canonical forecast is published automatically once
persisted -- see ``app.ppi.public_forecast`` -- there is no human approval step gating
publication. Human review exists only to flag a genuine data-integrity concern
(``FLAGGED``, which removes a forecast from public display) and to reset a flag once resolved; it
must never be used to selectively "approve" forecasts based on their contents, and every function
here is restricted to the ``reviewed_*`` columns -- never ``fair_value``, ``confidence``,
``should_abstain``, ``rationale``, ``key_uncertainties_json``, ``base_rate_notes``,
``raw_response``, or any other field that represents what the model actually produced.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import LLMForecast

REVIEW_STATUSES = ("UNREVIEWED", "FLAGGED")

# Fields a review action is permitted to change. Enforced defensively in tests, not just here.
_MUTABLE_REVIEW_FIELDS = {"reviewed_status", "reviewed_by", "reviewed_at", "review_notes"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def set_llm_forecast_review_status(
    session: Session,
    forecast: LLMForecast,
    status: str,
    reviewer: str,
    notes: str | None = None,
) -> LLMForecast:
    if status not in REVIEW_STATUSES:
        raise ValueError(f"Unknown review status: {status!r}. Must be one of {REVIEW_STATUSES}.")
    if not reviewer:
        raise ValueError("A reviewer identity is required.")

    forecast.reviewed_status = status
    forecast.reviewed_by = reviewer
    forecast.reviewed_at = utcnow()
    forecast.review_notes = notes
    session.flush()
    return forecast
