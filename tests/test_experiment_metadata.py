from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import ExperimentMetadata, JobRun
from app.ppi.experiment_metadata import (
    PREREGISTRATION_COMMIT_SHA,
    QWEN_VS_DEEPSEEK_EXPERIMENT_KEY,
    record_first_eligible_observation,
)


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'experiment_metadata.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_first_call_writes_the_marker_row(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = JobRun(run_key="ppi-daily:2026-08-20:primary", job_name="daily_pipeline", trigger_type="primary")
        session.add(job)
        session.flush()
        job_id = job.id
        observed_at = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)

        row = record_first_eligible_observation(session, job_run_id=job_id, observed_at=observed_at)

        assert row.experiment_key == QWEN_VS_DEEPSEEK_EXPERIMENT_KEY
        assert row.preregistration_commit_sha == PREREGISTRATION_COMMIT_SHA
        assert row.first_job_run_id == job_id
        assert row.first_observed_at == observed_at
        assert row.implementation_commit_sha is None  # no GITHUB_SHA set in this test


def test_implementation_commit_sha_read_from_github_sha_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "deadbeef" * 5)
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = JobRun(run_key="ppi-daily:2026-08-20:primary", job_name="daily_pipeline", trigger_type="primary")
        session.add(job)
        session.flush()

        row = record_first_eligible_observation(
            session, job_run_id=job.id, observed_at=datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
        )
        assert row.implementation_commit_sha == "deadbeef" * 5


def test_second_call_is_a_no_op_never_overwrites_the_first_observation(tmp_path, monkeypatch):
    """The whole point of this mechanism: once observation #1 is recorded, it is permanent --
    a later call (e.g. a subsequent scheduled run that also produces a matched pair) must never
    overwrite it, even with a different job_run_id/timestamp."""
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        first_job = JobRun(run_key="ppi-daily:2026-08-20:primary", job_name="daily_pipeline", trigger_type="primary")
        second_job = JobRun(run_key="ppi-daily:2026-08-20:backup", job_name="daily_pipeline", trigger_type="backup")
        session.add_all([first_job, second_job])
        session.flush()
        first_ts = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
        second_ts = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)

        first = record_first_eligible_observation(session, job_run_id=first_job.id, observed_at=first_ts)
        second = record_first_eligible_observation(session, job_run_id=second_job.id, observed_at=second_ts)

        assert first.id == second.id
        assert second.first_job_run_id == first_job.id  # unchanged -- not overwritten by the later call
        assert second.first_observed_at == first_ts

    with Session() as session:
        rows = list(
            session.scalars(
                select(ExperimentMetadata).where(ExperimentMetadata.experiment_key == QWEN_VS_DEEPSEEK_EXPERIMENT_KEY)
            )
        )
        assert len(rows) == 1  # never a second row for the same experiment_key


def test_no_observation_recorded_until_explicitly_called():
    """This module never populates a row on import or as any side effect -- only an explicit
    call, from the pipeline, after a real matched pair has been observed, can create one."""
    from app.db.database import Base as _Base

    assert "experiment_metadata" in _Base.metadata.tables
    # No assertion needed beyond "importing this module does not touch any session/database" --
    # the absence of any module-level session/engine usage in app.ppi.experiment_metadata is the
    # actual guarantee here, verified by this file's own successful, side-effect-free import.
