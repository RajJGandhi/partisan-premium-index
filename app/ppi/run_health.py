"""Forecast-outcome observability for one canonical (or any) pipeline run.

Derives every figure fresh from persisted records -- re-queries ``JobRun`` and its related
``LLMForecast``/``BlindIndexRun`` rows from the database rather than trusting in-memory counters
carried over from the run loop, so this reflects what was actually committed, not what the
process believed while it was running.

Kept separate from the pipeline and the Streamlit page so it is unit-testable without driving a
real pipeline run or a Streamlit script, and so the stdout summary, the GitHub Actions step
summary, and the admin UI all read from the exact same derivation instead of three copies that
could drift apart. Nothing here writes to the database, and nothing here ever touches
``DATABASE_URL`` or any other secret/connection value -- only aggregate counts and classification
labels already safe for public/operator display.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import BlindIndexRun, JobRun, LLMForecast
from app.ppi.blind_forecast import PRIMARY_SERIES_PROVIDERS


@dataclass(frozen=True)
class RunHealth:
    job_run_id: int
    run_key: str
    run_classification: str
    markets_attempted: int
    forecasts_ok: int
    forecasts_abstained: int
    forecasts_error: int
    evidence_attempted: int
    evidence_successful: int
    llm_fallback_count: int
    snapshots_persisted: int
    ppi_rows_persisted: int
    blind_index_rows_persisted: int

    def as_dict(self) -> dict[str, object]:
        return {
            "job_run_id": self.job_run_id,
            "run_key": self.run_key,
            "run_classification": self.run_classification,
            "markets_attempted": self.markets_attempted,
            "forecasts_ok": self.forecasts_ok,
            "forecasts_abstained": self.forecasts_abstained,
            "forecasts_error": self.forecasts_error,
            "evidence_classifications_attempted": self.evidence_attempted,
            "evidence_classifications_successful": self.evidence_successful,
            "llm_fallback_count": self.llm_fallback_count,
            "snapshots_persisted": self.snapshots_persisted,
            "ppi_rows_persisted": self.ppi_rows_persisted,
            "blind_index_rows_persisted": self.blind_index_rows_persisted,
        }


def compute_run_health(session: Session, job_run_id: int) -> RunHealth:
    """Derive forecast-outcome observability for one job run from persisted records.

    ``forecasts_error`` buckets everything that is neither ``OK`` nor ``ABSTAINED`` (``FAILED``,
    ``SKIPPED_PROVIDER``, or any unexpected status) -- none of those represent a fallback value,
    since ``LLMForecast`` has no fallback path at all; they mean no live probability was produced.

    ``evidence_successful`` is ``evidence_attempted`` (this run's newly inserted, newly
    classified items -- ``JobRun.evidence_discovered``) minus ``JobRun.evidence_classification_failed``;
    the subtraction can never go negative because the failure counter is only ever incremented
    inside the same "item was newly inserted" branch that increments the attempted counter.

    ``ppi_rows_persisted`` counts this run's ``LLMForecast`` rows that reached a persisted
    ``raw_ppi`` (i.e. were successfully joined to a market price after generation, per the
    canonical ``raw_ppi = polymarket_probability - llm_fair_value`` formula).

    ``blind_index_rows_persisted`` counts ``BlindIndexRun`` rows for this run's ``run_key``
    (upserted, so this is 0 or 1 for any real run).
    """
    job = session.get(JobRun, job_run_id)
    if job is None:
        raise ValueError(f"No JobRun with id={job_run_id}")

    # Scoped to the primary series only -- an experimental comparison series (e.g. openrouter)
    # sharing this job_run_id must never be mixed into the canonical series' own health counts.
    status_rows = session.execute(
        select(LLMForecast.status, func.count())
        .where(LLMForecast.job_run_id == job_run_id, LLMForecast.model_provider.in_(PRIMARY_SERIES_PROVIDERS))
        .group_by(LLMForecast.status)
    ).all()
    status_counts: dict[str, int] = {status: count for status, count in status_rows}
    forecasts_ok = status_counts.get("OK", 0)
    forecasts_abstained = status_counts.get("ABSTAINED", 0)
    forecasts_error = sum(count for status, count in status_counts.items() if status not in {"OK", "ABSTAINED"})

    blind_index_rows_persisted = (
        session.scalar(select(func.count()).select_from(BlindIndexRun).where(BlindIndexRun.run_key == job.run_key))
        or 0
    )
    ppi_rows_persisted = (
        session.scalar(
            select(func.count())
            .select_from(LLMForecast)
            .where(
                LLMForecast.job_run_id == job_run_id,
                LLMForecast.raw_ppi.is_not(None),
                LLMForecast.model_provider.in_(PRIMARY_SERIES_PROVIDERS),
            )
        )
        or 0
    )

    return RunHealth(
        job_run_id=job.id,
        run_key=job.run_key,
        run_classification=job.run_classification,
        markets_attempted=job.markets_attempted,
        forecasts_ok=forecasts_ok,
        forecasts_abstained=forecasts_abstained,
        forecasts_error=forecasts_error,
        evidence_attempted=job.evidence_discovered,
        evidence_successful=job.evidence_discovered - job.evidence_classification_failed,
        llm_fallback_count=job.llm_fallback_count,
        snapshots_persisted=job.snapshots_written,
        ppi_rows_persisted=ppi_rows_persisted,
        blind_index_rows_persisted=blind_index_rows_persisted,
    )


def render_run_health_markdown(health: RunHealth) -> str:
    """Render as a GitHub Actions step-summary-friendly Markdown table.

    Contains only counts and classification labels -- never raw model output, evidence text,
    prompts, or connection details.
    """
    rows = [
        ("Job run ID", health.job_run_id),
        ("Run key", health.run_key),
        ("Classification", health.run_classification),
        ("Markets attempted", health.markets_attempted),
        ("Forecasts OK", health.forecasts_ok),
        ("Forecasts abstained", health.forecasts_abstained),
        ("Forecasts error", health.forecasts_error),
        ("Evidence classifications attempted", health.evidence_attempted),
        ("Evidence classifications successful", health.evidence_successful),
        ("LLM fallback count", health.llm_fallback_count),
        ("Snapshots persisted", health.snapshots_persisted),
        ("PPI rows persisted", health.ppi_rows_persisted),
        ("Blind-index rows persisted", health.blind_index_rows_persisted),
    ]
    lines = ["## Forecast-outcome observability", "", "| Field | Value |", "| --- | ---: |"]
    lines.extend(f"| {label} | {value} |" for label, value in rows)
    return "\n".join(lines) + "\n"
