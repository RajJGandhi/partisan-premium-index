"""One canonical run record, opened before the fragile steps and guaranteed a terminal state.

The problem this solves: ``.github/workflows/ppi-daily.yml`` can fail in a prerequisite step
(provider check, ``migrate_db``, ``seed_production_markets``, ...) *before*
``scripts/run_ppi_daily.py`` ever runs, so the old behaviour left ``job_runs`` with no row at
all -- the database looked like nothing had even attempted to run (the 2026-08-27 -> 09-02
outage). Now the workflow calls :func:`open_run` right after runtime setup, so every canonical
attempt has a ``RUNNING`` row, and an ``if: always()`` finalize step calls :func:`finalize_run`
so that row always reaches ``OK`` / ``PARTIAL`` / ``FAILED``.

There is exactly one run record per ``(date, slot)`` -- keyed by the existing
``ppi-daily:<date>:<slot>`` ``run_key``. The workflow opens it; ``run_daily_pipeline`` re-opens
the *same* row (by run_key) and fills in the counters and its own terminal status; the finalize
step only writes a terminal status if nothing else already did. A workflow retry for the same
slot reuses the same row rather than creating a confusing duplicate.

Nothing here logs or persists a secret: ``workflow_run_id`` / ``git_sha`` come from GitHub
Actions context, ``error_stage`` is a fixed slug, and any error text stored in
``JobRun.sanitized_error`` is a short, type-name-prefixed summary -- never a connection string,
API key, Authorization header, or raw provider response.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.database import Base, engine
from app.db.models import JobRun, SourceRun
from app.ppi.run_classification import compute_run_classification

RUNNING = "RUNNING"
TERMINAL_STATUSES = frozenset({"OK", "PARTIAL", "FAILED"})

# Stages that run *after* run_daily_pipeline has already finalized its own row. A failure here
# means the data was recorded but publication did not complete -- annotate, never flip to FAILED.
POST_PIPELINE_STAGES = frozenset({"export_public", "export_v15", "frontend_build", "secret_scan", "deploy"})

# Additive, nullable columns this module needs. Kept in sync with scripts/migrate_db.py's
# ADDITIVE_COLUMNS["job_runs"]; duplicated here (not imported) so `open_run` can guarantee the
# columns exist even though it runs *before* the "Run database migrations" workflow step.
_LIFECYCLE_COLUMNS: dict[str, str] = {
    "workflow_run_id": "VARCHAR(64)",
    "git_sha": "VARCHAR(64)",
    "error_stage": "VARCHAR(80)",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def derive_run_key(trigger_type: str, run_date: date | None = None) -> str:
    """The canonical run identity: ``ppi-daily:<YYYY-MM-DD>:<slot>``.

    Same formula run_daily_pipeline uses, so the workflow's ``start`` step and the application
    converge on one row without passing an id around.
    """
    day = run_date or utcnow().date()
    return f"ppi-daily:{day.isoformat()}:{trigger_type}"


def ensure_lifecycle_columns(target_engine: Engine | None = None) -> list[str]:
    """Idempotently make sure ``job_runs`` exists and has the lifecycle columns.

    Safe to call on every invocation and before the full migration runs: ``create_all`` is a
    no-op for existing tables, and each ``ADD COLUMN`` is guarded by a live column inspection.
    Returns the names of any columns it actually added (empty on the common path).
    """
    eng = target_engine or engine
    Base.metadata.tables[JobRun.__tablename__].create(bind=eng, checkfirst=True)
    inspector = inspect(eng)
    if "job_runs" not in inspector.get_table_names():
        return []
    existing = {col["name"] for col in inspector.get_columns("job_runs")}
    added: list[str] = []
    with eng.begin() as conn:
        for name, ddl in _LIFECYCLE_COLUMNS.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE job_runs ADD COLUMN {name} {ddl}"))
                added.append(name)
    return added


def _reset_counters(job: JobRun, *, now: datetime) -> None:
    """Return a reused row to a clean RUNNING state for a fresh attempt.

    Mirrors run_daily_pipeline's own retry reset so the two entry points behave identically.
    """
    job.status = RUNNING
    job.started_at = now
    job.finished_at = None
    job.sanitized_error = None
    job.error_stage = None
    job.metadata_json = None
    job.markets_attempted = 0
    job.markets_succeeded = 0
    job.evidence_discovered = 0
    job.evidence_relevant = 0
    job.evidence_classification_failed = 0
    job.llm_fallback_count = 0
    job.proposals_created = 0
    job.snapshots_written = 0
    job.error_count = 0
    job.llm_forecasts_attempted = 0
    job.llm_forecasts_succeeded = 0
    job.llm_forecasts_abstained = 0
    job.llm_forecasts_failed = 0
    job.llm_forecasts_skipped = 0


def open_run(
    session: Session,
    *,
    run_key: str,
    trigger_type: str,
    pipeline_mode: str,
    job_name: str = "daily_pipeline",
    workflow_run_id: str | None = None,
    git_sha: str | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> tuple[JobRun, str]:
    """Create, re-attach to, or reset the one ``job_runs`` row for ``run_key``; leave it RUNNING.

    Outcomes:
      * ``created``          -- no row existed; a new RUNNING row was inserted.
      * ``reattached``       -- the row was already RUNNING (opened moments ago by the workflow's
                               own start step); counters cleared, ``started_at`` preserved.
      * ``reset``            -- the row was in a terminal state (a retry / ``--force`` of an
                               already-OK slot); counters cleared, ``started_at`` moved to now.
      * ``already_complete`` -- the row is OK and ``force`` is false; nothing is touched. The
                               caller should short-circuit (this is the idempotency guarantee).

    The row is committed by the caller. ``workflow_run_id`` / ``git_sha`` are only written when
    provided, so the application re-opening a workflow-opened row never nulls them out.
    """
    now = now or utcnow()
    existing = session.scalar(select(JobRun).where(JobRun.run_key == run_key))

    if existing is not None and existing.status == "OK" and not force:
        return existing, "already_complete"

    if existing is not None:
        job = existing
        session.execute(delete(SourceRun).where(SourceRun.job_run_id == job.id))
        if job.status == RUNNING:
            outcome = "reattached"
            preserved_started_at = job.started_at
            _reset_counters(job, now=now)
            job.started_at = preserved_started_at  # keep the real attempt start
        else:
            outcome = "reset"
            _reset_counters(job, now=now)
    else:
        job = JobRun(run_key=run_key, job_name=job_name, trigger_type=trigger_type, started_at=now)
        job.status = RUNNING
        session.add(job)
        outcome = "created"

    job.trigger_type = trigger_type
    job.pipeline_mode = pipeline_mode
    if workflow_run_id is not None:
        job.workflow_run_id = workflow_run_id
    if git_sha is not None:
        job.git_sha = git_sha
    session.flush()
    return job, outcome


def _sanitized_stage_error(conclusion: str, error_stage: str | None) -> str:
    stage = error_stage or "unknown"
    return (
        f"Workflow {conclusion or 'failure'} at stage '{stage}' before the pipeline finalized "
        "its own run record."
    )


def finalize_run(
    session: Session,
    *,
    run_key: str,
    workflow_conclusion: str,
    error_stage: str | None = None,
    workflow_run_id: str | None = None,
    git_sha: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Guarantee a terminal status for ``run_key`` -- the workflow's ``if: always()`` backstop.

    ``workflow_conclusion`` is GitHub's ``${{ job.status }}``: ``success`` / ``failure`` /
    ``cancelled`` (or ``""`` locally). This never *overwrites* a terminal status the application
    already set -- it only closes a row still stuck at RUNNING, or records a post-pipeline
    (publish/deploy) failure that happened after the data was already committed.

    Returns ``{"action": ..., "run_key": ..., "status": ..., "job_run_id": ...}``.
    """
    now = now or utcnow()
    conclusion = (workflow_conclusion or "").strip().lower()
    success = conclusion == "success"
    error_stage = (error_stage or "").strip() or None

    job = session.scalar(select(JobRun).where(JobRun.run_key == run_key))

    if job is None:
        if success:
            # A successful run always opens its own record; nothing to reconcile.
            return {"action": "noop_no_row", "run_key": run_key, "status": None, "job_run_id": None}
        job = JobRun(
            run_key=run_key,
            job_name="daily_pipeline",
            trigger_type=run_key.rsplit(":", 1)[-1],
            started_at=now,
            finished_at=now,
            status="FAILED",
            pipeline_mode="strict_llm_only",
            run_classification="failed",
            error_stage=error_stage or "start_record",
            sanitized_error=(
                f"Workflow {conclusion or 'failure'}; the run record was never opened "
                "(failed at or before the start step)."
            ),
            workflow_run_id=workflow_run_id,
            git_sha=git_sha,
            error_count=1,
        )
        session.add(job)
        session.flush()
        return {"action": "created_failed", "run_key": run_key, "status": job.status, "job_run_id": job.id}

    if workflow_run_id is not None and not job.workflow_run_id:
        job.workflow_run_id = workflow_run_id
    if git_sha is not None and not job.git_sha:
        job.git_sha = git_sha

    if job.status == RUNNING:
        if success:
            # Defensive: the application should have set OK itself. Close it rather than leave
            # a phantom RUNNING row.
            job.status = "OK"
            job.finished_at = now
            action = "closed_ok_defensive"
        else:
            resolved_stage = error_stage or ("cancelled" if conclusion == "cancelled" else "unknown")
            job.status = "FAILED"
            job.finished_at = now
            job.error_stage = resolved_stage
            job.sanitized_error = _sanitized_stage_error(conclusion, resolved_stage)
            job.error_count = max(job.error_count, 1)
            action = "closed_failed"
        job.run_classification = compute_run_classification(session, job, run_key)
        session.flush()
        return {"action": action, "run_key": run_key, "status": job.status, "job_run_id": job.id}

    # Already terminal -- the application finalized it. Do not flip OK<->FAILED.
    if not success and error_stage in POST_PIPELINE_STAGES:
        job.error_stage = error_stage
        meta: dict[str, Any] = {}
        if job.metadata_json:
            try:
                meta = json.loads(job.metadata_json)
            except (ValueError, TypeError):
                meta = {}
        meta["post_pipeline_failure"] = error_stage
        job.metadata_json = json.dumps(meta, default=str)
        session.flush()
        return {
            "action": "annotated_post_pipeline_failure",
            "run_key": run_key,
            "status": job.status,
            "job_run_id": job.id,
        }

    session.flush()
    return {"action": "noop_already_terminal", "run_key": run_key, "status": job.status, "job_run_id": job.id}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def latest_canonical_success(session: Session) -> JobRun | None:
    """Most recent run that is safe to call "the last successful canonical observation":
    ``status == "OK"``, ``run_classification == "canonical"``, not superseded."""
    return session.scalar(
        select(JobRun)
        .where(
            JobRun.status == "OK",
            JobRun.run_classification == "canonical",
            JobRun.superseded_by_id.is_(None),
        )
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )


def run_status_summary(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Small, DB-derived run-health block for the public system-status export.

    Every value is computed here from ``job_runs`` -- never hardcoded, never a secret. Deliberately
    compact (last attempt / last success / last status / markets completed / errors), not a
    metrics platform.
    """
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    last_attempt = session.scalar(select(JobRun).order_by(JobRun.started_at.desc()).limit(1))
    canonical_success = latest_canonical_success(session)
    any_success = session.scalar(
        select(JobRun).where(JobRun.status == "OK").order_by(JobRun.started_at.desc()).limit(1)
    )

    consecutive_failed = 0
    for row in session.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(20)):
        if row.status == "FAILED":
            consecutive_failed += 1
        else:
            break

    hours_since_success = None
    if canonical_success and canonical_success.finished_at:
        finished = canonical_success.finished_at
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=UTC)
        hours_since_success = round((now - finished).total_seconds() / 3600.0, 2)

    return {
        "last_attempt": _iso(last_attempt.started_at) if last_attempt else None,
        "last_attempt_run_key": last_attempt.run_key if last_attempt else None,
        "last_status": last_attempt.status if last_attempt else "NO_RUNS",
        "last_error_stage": last_attempt.error_stage if last_attempt else None,
        "error_count": last_attempt.error_count if last_attempt else 0,
        "last_success": _iso(any_success.finished_at) if any_success else None,
        "last_canonical_success": _iso(canonical_success.finished_at) if canonical_success else None,
        "last_canonical_success_run_key": canonical_success.run_key if canonical_success else None,
        "markets_completed": canonical_success.markets_succeeded if canonical_success else 0,
        "hours_since_canonical_success": hours_since_success,
        "consecutive_failed_attempts": consecutive_failed,
    }
