"""End-to-end lifecycle observability: every canonical attempt is an auditable RUNNING -> OK
or RUNNING -> FAILED row, even when the workflow dies before scripts/run_ppi_daily.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import JobRun
from app.ppi.job_run_lifecycle import (
    derive_run_key,
    ensure_lifecycle_columns,
    finalize_run,
    latest_canonical_success,
    open_run,
    run_status_summary,
)


def _session_factory(tmp_path, name="lifecycle.db"):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False, future=True), engine


def _finish_ok(session, job: JobRun, *, markets=12, now=None) -> None:
    """Stand-in for what run_daily_pipeline does at the end of a clean run."""
    job.markets_attempted = markets
    job.markets_succeeded = markets
    job.snapshots_written = markets
    job.status = "OK"
    job.error_count = 0
    job.finished_at = now or datetime.now(UTC)
    job.run_classification = "canonical" if job.trigger_type in {"primary", "backup"} else "adhoc"


# --- 1. successful lifecycle: RUNNING -> OK ---------------------------------------------------
def test_successful_lifecycle_running_then_ok(tmp_path):
    Session, _ = _session_factory(tmp_path)
    run_key = derive_run_key("primary", datetime(2026, 9, 2).date())

    with Session.begin() as session:
        job, outcome = open_run(
            session,
            run_key=run_key,
            trigger_type="primary",
            pipeline_mode="strict_llm_only",
            workflow_run_id="123/1",
            git_sha="abc123",
        )
        assert outcome == "created"
        assert job.status == "RUNNING"
        opened_started_at = job.started_at.replace(tzinfo=None)  # sqlite drops tzinfo on reload

    # run_daily_pipeline re-attaches to the SAME row by run_key.
    with Session.begin() as session:
        job, outcome = open_run(session, run_key=run_key, trigger_type="primary", pipeline_mode="strict_llm_only")
        assert outcome == "reattached"
        assert job.started_at.replace(tzinfo=None) == opened_started_at  # attempt start preserved, not bumped
        assert job.workflow_run_id == "123/1"  # not nulled by the app re-open
        _finish_ok(session, job)

    # Finalizer sees a terminal status -> no-op.
    with Session.begin() as session:
        result = finalize_run(session, run_key=run_key, workflow_conclusion="success")
        assert result["action"] == "noop_already_terminal"

    with Session() as session:
        rows = list(session.scalars(select(JobRun).where(JobRun.run_key == run_key)))
        assert len(rows) == 1
        assert rows[0].status == "OK"
        assert rows[0].error_stage is None


# --- 2. failed lifecycle: RUNNING -> FAILED (finalizer closes a stuck RUNNING row) ----------
def test_failed_lifecycle_running_then_failed(tmp_path):
    Session, _ = _session_factory(tmp_path)
    run_key = derive_run_key("backup", datetime(2026, 9, 2).date())

    with Session.begin() as session:
        job, _ = open_run(session, run_key=run_key, trigger_type="backup", pipeline_mode="strict_llm_only")
        assert job.status == "RUNNING"

    # Pipeline step never finalized (hard crash / killed). always() finalizer runs:
    with Session.begin() as session:
        result = finalize_run(
            session, run_key=run_key, workflow_conclusion="failure", error_stage="run_pipeline"
        )
        assert result["action"] == "closed_failed"

    with Session() as session:
        row = session.scalar(select(JobRun).where(JobRun.run_key == run_key))
        assert row.status == "FAILED"
        assert row.error_stage == "run_pipeline"
        assert row.finished_at is not None
        assert row.run_classification == "failed"


# --- 3. failure BEFORE forecast execution (the 2026-08-27 outage shape) ---------------------
def test_failure_before_forecast_execution_still_leaves_a_failed_row(tmp_path):
    """The workflow opened the record, then the provider-check step failed -- run_ppi_daily.py
    never ran. Old behaviour: no row at all. New behaviour: a FAILED row with error_stage."""
    Session, _ = _session_factory(tmp_path)
    run_key = derive_run_key("primary", datetime(2026, 9, 3).date())

    with Session.begin() as session:
        open_run(
            session,
            run_key=run_key,
            trigger_type="primary",
            pipeline_mode="strict_llm_only",
            workflow_run_id="200/1",
            git_sha="deadbeef",
        )

    # provider_check / migrate_db style failure -- no pipeline, no forecasts.
    with Session.begin() as session:
        finalize_run(session, run_key=run_key, workflow_conclusion="failure", error_stage="provider_check")

    with Session() as session:
        row = session.scalar(select(JobRun).where(JobRun.run_key == run_key))
        assert row is not None, "a pre-pipeline failure must never vanish from job_runs"
        assert row.status == "FAILED"
        assert row.error_stage == "provider_check"
        assert row.markets_attempted == 0
        assert row.workflow_run_id == "200/1"
        assert row.git_sha == "deadbeef"


def test_finalizer_creates_a_failed_row_when_even_the_start_step_failed(tmp_path):
    """If `start` itself could not open a row, the always() finalizer still records the attempt."""
    Session, _ = _session_factory(tmp_path)
    run_key = derive_run_key("primary", datetime(2026, 9, 4).date())

    with Session.begin() as session:
        result = finalize_run(
            session, run_key=run_key, workflow_conclusion="failure", error_stage="install_deps"
        )
        assert result["action"] == "created_failed"

    with Session() as session:
        row = session.scalar(select(JobRun).where(JobRun.run_key == run_key))
        assert row.status == "FAILED"
        assert row.run_classification == "failed"
        assert row.error_stage == "install_deps"


# --- 4. latest canonical success query -----------------------------------------------------
def test_latest_canonical_success_ignores_failed_adhoc_and_superseded(tmp_path):
    Session, _ = _session_factory(tmp_path)
    base = datetime(2026, 9, 1, 13, 0, tzinfo=UTC)

    with Session.begin() as session:
        # older canonical OK
        good_old = JobRun(
            run_key="ppi-daily:2026-08-31:primary", job_name="daily_pipeline", trigger_type="primary",
            status="OK", pipeline_mode="strict_llm_only", run_classification="canonical",
            started_at=base - timedelta(days=1), finished_at=base - timedelta(days=1) + timedelta(minutes=6),
            markets_succeeded=12,
        )
        # newer canonical OK -- this is the expected answer
        good_new = JobRun(
            run_key="ppi-daily:2026-09-01:primary", job_name="daily_pipeline", trigger_type="primary",
            status="OK", pipeline_mode="strict_llm_only", run_classification="canonical",
            started_at=base, finished_at=base + timedelta(minutes=7), markets_succeeded=11,
        )
        # newest row overall, but FAILED
        failed_newer = JobRun(
            run_key="ppi-daily:2026-09-02:primary", job_name="daily_pipeline", trigger_type="primary",
            status="FAILED", pipeline_mode="strict_llm_only", run_classification="failed",
            started_at=base + timedelta(days=1), finished_at=base + timedelta(days=1),
        )
        # newer OK but adhoc (manual), not a scheduled canonical observation
        adhoc_newer = JobRun(
            run_key="ppi-daily:2026-09-02:adhoc", job_name="daily_pipeline", trigger_type="adhoc",
            status="OK", pipeline_mode="strict_llm_only", run_classification="adhoc",
            started_at=base + timedelta(days=1, hours=1), finished_at=base + timedelta(days=1, hours=1),
        )
        session.add_all([good_old, good_new, failed_newer, adhoc_newer])

    with Session() as session:
        latest = latest_canonical_success(session)
        assert latest is not None
        assert latest.run_key == "ppi-daily:2026-09-01:primary"

    # now supersede that one -> falls back to the older canonical OK
    with Session.begin() as session:
        row = session.scalar(select(JobRun).where(JobRun.run_key == "ppi-daily:2026-09-01:primary"))
        row.superseded_by_id = row.id  # any non-null value
    with Session() as session:
        latest = latest_canonical_success(session)
        assert latest.run_key == "ppi-daily:2026-08-31:primary"


# --- 5. sanitized error handling ---------------------------------------------------------
def test_finalize_error_text_is_a_sanitized_slug_summary_not_a_secret(tmp_path):
    Session, _ = _session_factory(tmp_path)
    run_key = derive_run_key("primary", datetime(2026, 9, 5).date())
    with Session.begin() as session:
        open_run(session, run_key=run_key, trigger_type="primary", pipeline_mode="strict_llm_only")
    with Session.begin() as session:
        finalize_run(session, run_key=run_key, workflow_conclusion="failure", error_stage="migrate_db")
    with Session() as session:
        row = session.scalar(select(JobRun).where(JobRun.run_key == run_key))
        text_blob = f"{row.sanitized_error} {row.error_stage} {row.metadata_json}"
        for forbidden in ("postgres://", "postgresql://", "://", "@", "password", "api_key", "authorization", "secret"):
            assert forbidden.lower() not in text_blob.lower()
        assert row.sanitized_error == (
            "Workflow failure at stage 'migrate_db' before the pipeline finalized its own run record."
        )


# --- 6. no duplicate run record for the same canonical identity ------------------------------
def test_no_duplicate_row_for_same_run_key_across_open_and_finalize_and_retry(tmp_path):
    Session, _ = _session_factory(tmp_path)
    run_key = derive_run_key("primary", datetime(2026, 9, 6).date())

    # attempt 1: workflow opens, provider check fails, finalizer closes as FAILED
    with Session.begin() as session:
        open_run(session, run_key=run_key, trigger_type="primary", pipeline_mode="strict_llm_only",
                 workflow_run_id="900/1", git_sha="sha1")
    with Session.begin() as session:
        finalize_run(session, run_key=run_key, workflow_conclusion="failure", error_stage="provider_check")

    # attempt 2 (a retry -- github.run_attempt 2): same run_key, reuses the row, resets to RUNNING
    with Session.begin() as session:
        job, outcome = open_run(session, run_key=run_key, trigger_type="primary",
                                pipeline_mode="strict_llm_only", workflow_run_id="900/2", git_sha="sha1")
        assert outcome == "reset"
        assert job.status == "RUNNING"
        assert job.error_stage is None  # cleared for the fresh attempt
        assert job.workflow_run_id == "900/2"
        _finish_ok(session, job)
    with Session.begin() as session:
        finalize_run(session, run_key=run_key, workflow_conclusion="success")

    with Session() as session:
        rows = list(session.scalars(select(JobRun).where(JobRun.run_key == run_key)))
        assert len(rows) == 1, "a retry of the same slot must not create a second lifecycle row"
        assert rows[0].status == "OK"


def test_open_run_is_idempotent_for_an_already_ok_slot_without_force(tmp_path):
    Session, _ = _session_factory(tmp_path)
    run_key = derive_run_key("primary", datetime(2026, 9, 7).date())
    with Session.begin() as session:
        job, _ = open_run(session, run_key=run_key, trigger_type="primary", pipeline_mode="strict_llm_only")
        _finish_ok(session, job)
        first_id = job.id
    with Session.begin() as session:
        job, outcome = open_run(session, run_key=run_key, trigger_type="primary", pipeline_mode="strict_llm_only")
        assert outcome == "already_complete"
        assert job.id == first_id
        assert job.status == "OK"  # untouched


# --- finalizer must never overwrite a status the application already set --------------------
def test_finalizer_never_flips_an_application_set_ok_to_failed(tmp_path):
    """Pipeline committed OK, but a later publish/deploy step failed. The DB write succeeded --
    status stays OK, the post-pipeline failure is annotated, never a false FAILED."""
    Session, _ = _session_factory(tmp_path)
    run_key = derive_run_key("primary", datetime(2026, 9, 8).date())
    with Session.begin() as session:
        job, _ = open_run(session, run_key=run_key, trigger_type="primary", pipeline_mode="strict_llm_only")
        _finish_ok(session, job)
    with Session.begin() as session:
        result = finalize_run(session, run_key=run_key, workflow_conclusion="failure", error_stage="deploy")
        assert result["action"] == "annotated_post_pipeline_failure"
    with Session() as session:
        row = session.scalar(select(JobRun).where(JobRun.run_key == run_key))
        assert row.status == "OK"  # not clobbered
        assert row.error_stage == "deploy"
        assert '"post_pipeline_failure": "deploy"' in (row.metadata_json or "")


def test_finalizer_defensively_closes_a_stuck_running_row_on_success(tmp_path):
    Session, _ = _session_factory(tmp_path)
    run_key = derive_run_key("primary", datetime(2026, 9, 9).date())
    with Session.begin() as session:
        open_run(session, run_key=run_key, trigger_type="primary", pipeline_mode="strict_llm_only")
    with Session.begin() as session:
        result = finalize_run(session, run_key=run_key, workflow_conclusion="success")
        assert result["action"] == "closed_ok_defensive"
    with Session() as session:
        assert session.scalar(select(JobRun).where(JobRun.run_key == run_key)).status == "OK"


# --- run_status_summary (the DB-derived export block) --------------------------------------
def test_run_status_summary_reports_last_attempt_success_and_markets(tmp_path):
    Session, _ = _session_factory(tmp_path)
    now = datetime(2026, 9, 2, 18, 0, tzinfo=UTC)
    with Session.begin() as session:
        ok = JobRun(
            run_key="ppi-daily:2026-09-02:primary", job_name="daily_pipeline", trigger_type="primary",
            status="OK", pipeline_mode="strict_llm_only", run_classification="canonical",
            started_at=now - timedelta(hours=5), finished_at=now - timedelta(hours=5) + timedelta(minutes=7),
            markets_attempted=12, markets_succeeded=12,
        )
        failed = JobRun(
            run_key="ppi-daily:2026-09-02:backup", job_name="daily_pipeline", trigger_type="backup",
            status="FAILED", pipeline_mode="strict_llm_only", run_classification="failed",
            started_at=now - timedelta(hours=1), finished_at=now - timedelta(hours=1),
            error_stage="provider_check", error_count=1,
        )
        session.add_all([ok, failed])

    with Session() as session:
        summary = run_status_summary(session, now=now)

    assert summary["last_status"] == "FAILED"
    assert summary["last_error_stage"] == "provider_check"
    assert summary["last_attempt_run_key"] == "ppi-daily:2026-09-02:backup"
    assert summary["last_canonical_success_run_key"] == "ppi-daily:2026-09-02:primary"
    assert summary["markets_completed"] == 12
    assert summary["hours_since_canonical_success"] == 4.88  # 5h - 7min
    assert summary["consecutive_failed_attempts"] == 1


def test_run_status_summary_on_an_empty_database(tmp_path):
    Session, _ = _session_factory(tmp_path)
    with Session() as session:
        summary = run_status_summary(session)
    assert summary["last_status"] == "NO_RUNS"
    assert summary["last_success"] is None
    assert summary["markets_completed"] == 0
    assert summary["consecutive_failed_attempts"] == 0


# --- ensure_lifecycle_columns -----------------------------------------------------------
def test_ensure_lifecycle_columns_adds_missing_columns_to_a_legacy_table(tmp_path):
    """Simulates a job_runs table created before this change (no workflow_run_id/git_sha/
    error_stage). ensure_lifecycle_columns must add them so `start` works before migrate_db.py."""
    db = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE job_runs (id INTEGER PRIMARY KEY, run_key VARCHAR(150) UNIQUE, "
                "job_name VARCHAR(100), trigger_type VARCHAR(30), started_at DATETIME, "
                "finished_at DATETIME, status VARCHAR(30) DEFAULT 'RUNNING', "
                "markets_attempted INTEGER DEFAULT 0, markets_succeeded INTEGER DEFAULT 0, "
                "evidence_discovered INTEGER DEFAULT 0, evidence_relevant INTEGER DEFAULT 0, "
                "proposals_created INTEGER DEFAULT 0, snapshots_written INTEGER DEFAULT 0, "
                "error_count INTEGER DEFAULT 0, sanitized_error TEXT, metadata_json TEXT, "
                "llm_forecasts_attempted INTEGER DEFAULT 0, llm_forecasts_succeeded INTEGER DEFAULT 0, "
                "llm_forecasts_abstained INTEGER DEFAULT 0, llm_forecasts_failed INTEGER DEFAULT 0, "
                "llm_forecasts_skipped INTEGER DEFAULT 0, evidence_classification_failed INTEGER DEFAULT 0, "
                "llm_fallback_count INTEGER DEFAULT 0, "
                "pipeline_mode VARCHAR(40) DEFAULT 'standard_mixed_fallback_allowed', "
                "run_classification VARCHAR(30) DEFAULT 'adhoc', superseded_by_id INTEGER)"
            )
        )

    before = {c["name"] for c in inspect(engine).get_columns("job_runs")}
    assert "workflow_run_id" not in before

    added = ensure_lifecycle_columns(engine)
    assert set(added) == {"workflow_run_id", "git_sha", "error_stage"}

    after = {c["name"] for c in inspect(engine).get_columns("job_runs")}
    assert {"workflow_run_id", "git_sha", "error_stage"} <= after

    # idempotent
    assert ensure_lifecycle_columns(engine) == []
