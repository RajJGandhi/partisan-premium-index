"""Computes and records which of six categories a pipeline run belongs to.

- ``canonical``: strict_llm_only, succeeded, a scheduled primary/backup slot, zero contaminated
  forecasts. The only category safe to publish (see ``scripts/export_public_bundle.py``).
- ``noncanonical_mixed``: standard mode -- evidence classification may have silently fallen back
  to the deterministic classifier.
- ``contaminated``: strict_llm_only, but at least one forecast's evidence packet included an item
  that was not itself classified by a live model (reused via content-hash dedup from an earlier
  non-strict or failed run).
- ``failed``: the run did not complete (``JobRun.status == "FAILED"``).
- ``adhoc``: a manual/ad hoc invocation outside the primary/backup schedule (any other
  trigger_type). Real live data, just not one of the twice-daily canonical observations.
- ``superseded``: an overlay, not a base category -- see ``mark_job_run_superseded``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobRun, LLMForecast

CANONICAL_TRIGGER_TYPES = ("primary", "backup")

RUN_CLASSIFICATIONS = (
    "canonical",
    "noncanonical_mixed",
    "contaminated",
    "failed",
    "adhoc",
)


def compute_run_classification(session: Session, job: JobRun, run_key: str) -> str:
    """Quality signals (mixed/contaminated) take priority over the scheduling signal (adhoc), so
    e.g. a manually-triggered strict run that turns out contaminated is reported as
    "contaminated", not masked by "adhoc" -- and a genuinely clean strict run outside the
    schedule is "adhoc", not falsely promoted to "canonical"."""
    if job.status == "FAILED":
        return "failed"
    if job.pipeline_mode != "strict_llm_only":
        return "noncanonical_mixed"
    forecasts = list(session.scalars(select(LLMForecast).where(LLMForecast.run_key == run_key)))
    if any(f.evidence_all_live_classified is False for f in forecasts):
        return "contaminated"
    if job.trigger_type not in CANONICAL_TRIGGER_TYPES:
        return "adhoc"
    return "canonical"


def mark_job_run_superseded(session: Session, old_job_run: JobRun, new_job_run: JobRun) -> None:
    """Record that ``new_job_run`` replaces ``old_job_run`` for reporting/publication purposes.

    Never deletes or edits ``old_job_run``'s own results (snapshots, forecasts, evidence, the
    blind index row) -- only sets a pointer so the Streamlit UI and public export can prefer the
    newer run without losing the historical record.
    """
    if old_job_run.id == new_job_run.id:
        raise ValueError("A job run cannot supersede itself")
    old_job_run.superseded_by_id = new_job_run.id
    session.flush()


def is_canonical_and_current(job: JobRun) -> bool:
    """True only for a run that is both canonical and not superseded by a later one."""
    return job.run_classification == "canonical" and job.superseded_by_id is None
