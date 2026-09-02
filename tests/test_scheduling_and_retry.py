from __future__ import annotations

from contextlib import contextmanager
from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.database import Base
from app.db.models import JobRun


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'scheduling.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def _fake_get_session(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _patched_pipeline_module(monkeypatch, Session):
    from app.ppi import pipeline as pipeline_module

    settings = Settings(llm_provider="deterministic", db_preflight_enabled=False)
    monkeypatch.setattr(pipeline_module, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline_module, "init_db", lambda: None)
    monkeypatch.setattr(pipeline_module, "get_session", lambda: _fake_get_session(Session))
    return pipeline_module


def test_run_key_is_deterministic_for_the_same_day_and_slot(tmp_path, monkeypatch):
    """Two independent calls for the same (date, trigger_type) must compute the identical
    run_key -- this is what makes a retry recognizable as "the same logical run" rather than a
    new one, regardless of how much wall-clock time passes between the two calls."""
    pipeline_module = _patched_pipeline_module(monkeypatch, _session_factory(tmp_path))

    day = date(2026, 8, 6)
    result_a = pipeline_module.run_daily_pipeline(
        "primary", run_date=day, lock_path=tmp_path / "a.lock"
    )
    result_b = pipeline_module.run_daily_pipeline(
        "primary", run_date=day, lock_path=tmp_path / "a.lock"
    )

    assert result_a["run_key"] == "ppi-daily:2026-08-06:primary"
    assert result_b["run_key"] == result_a["run_key"]
    assert result_b["job_run_id"] == result_a["job_run_id"]


def test_rerun_without_force_short_circuits_as_already_complete(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    pipeline_module = _patched_pipeline_module(monkeypatch, Session)

    day = date(2026, 8, 6)
    first = pipeline_module.run_daily_pipeline("primary", run_date=day, lock_path=tmp_path / "a.lock")
    assert first["status"] == "OK"

    second = pipeline_module.run_daily_pipeline("primary", run_date=day, lock_path=tmp_path / "b.lock")
    assert second["status"] == "ALREADY_COMPLETE"
    assert second["job_run_id"] == first["job_run_id"]

    # No duplicate JobRun row was created for the same run_key.
    with Session() as session:
        matches = list(session.scalars(select(JobRun).where(JobRun.run_key == first["run_key"])))
        assert len(matches) == 1


def test_primary_and_backup_are_distinct_job_runs_on_the_same_day(tmp_path, monkeypatch):
    """The backup run must remain its own scheduled observation, never silently replacing (or
    being merged into) the primary run's JobRun row, even though both fire on the same date."""
    Session = _session_factory(tmp_path)
    pipeline_module = _patched_pipeline_module(monkeypatch, Session)

    day = date(2026, 8, 6)
    primary = pipeline_module.run_daily_pipeline("primary", run_date=day, lock_path=tmp_path / "p.lock")
    backup = pipeline_module.run_daily_pipeline("backup", run_date=day, lock_path=tmp_path / "b.lock")

    assert primary["run_key"] == "ppi-daily:2026-08-06:primary"
    assert backup["run_key"] == "ppi-daily:2026-08-06:backup"
    assert primary["job_run_id"] != backup["job_run_id"]

    with Session() as session:
        rows = list(session.scalars(select(JobRun).order_by(JobRun.id)))
        assert {r.run_key for r in rows} == {primary["run_key"], backup["run_key"]}
        assert len(rows) == 2


def test_failed_primary_retry_does_not_touch_backups_row(tmp_path, monkeypatch):
    """Retrying a failed primary run (force=True) must only affect the primary JobRun row --
    a completed backup run for the same day must be left exactly as it was."""
    Session = _session_factory(tmp_path)
    pipeline_module = _patched_pipeline_module(monkeypatch, Session)

    day = date(2026, 8, 6)
    backup = pipeline_module.run_daily_pipeline("backup", run_date=day, lock_path=tmp_path / "b.lock")
    assert backup["status"] == "OK"

    with Session.begin() as session:
        backup_job = session.get(JobRun, backup["job_run_id"])
        backup_finished_at_before = backup_job.finished_at

    retried_primary = pipeline_module.run_daily_pipeline(
        "primary", run_date=day, force=True, lock_path=tmp_path / "p.lock"
    )
    assert retried_primary["run_key"] == "ppi-daily:2026-08-06:primary"

    with Session() as session:
        backup_job = session.get(JobRun, backup["job_run_id"])
        assert backup_job.finished_at == backup_finished_at_before
        assert backup_job.status == "OK"
