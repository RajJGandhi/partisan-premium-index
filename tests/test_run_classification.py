from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import JobRun, LLMForecast, Market
from app.ppi.run_classification import compute_run_classification, is_canonical_and_current, mark_job_run_superseded


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'classification.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _job(**kwargs) -> JobRun:
    defaults = dict(
        run_key="ppi-daily:2026-08-06:primary",
        job_name="daily_pipeline",
        trigger_type="primary",
        status="OK",
        pipeline_mode="strict_llm_only",
    )
    defaults.update(kwargs)
    return JobRun(**defaults)


def test_failed_run_is_classified_failed_regardless_of_mode(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job(status="FAILED", trigger_type="primary", pipeline_mode="strict_llm_only")
        session.add(job)
        session.flush()
        assert compute_run_classification(session, job, job.run_key) == "failed"


def test_standard_mode_run_is_noncanonical_mixed_even_on_the_real_schedule(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job(trigger_type="primary", pipeline_mode="standard_mixed_fallback_allowed")
        session.add(job)
        session.flush()
        assert compute_run_classification(session, job, job.run_key) == "noncanonical_mixed"


def test_strict_run_with_contaminated_evidence_is_contaminated_even_if_adhoc(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        market = Market(platform_market_id="1", tracking_id="T-1", question="Q?", enabled=True)
        session.add(market)
        session.flush()
        job = _job(run_key="ppi-daily:2026-08-06:manual-retry", trigger_type="manual-retry")
        session.add(job)
        session.flush()
        session.add(
            LLMForecast(
                market_id=market.id,
                run_slot="2026-08-06:adhoc-000000",
                run_key=job.run_key,
                trigger_type=job.trigger_type,
                model_provider="ollama",
                model_name="qwen3:8b",
                prompt_version="fair_value_v0.1",
                status="OK",
                evidence_all_live_classified=False,
            )
        )
        session.flush()
        # Contamination is detected even though trigger_type isn't primary/backup -- quality
        # signals must not be masked by the scheduling signal.
        assert compute_run_classification(session, job, job.run_key) == "contaminated"


def test_strict_clean_run_outside_the_schedule_is_adhoc_not_canonical(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job(run_key="ppi-daily:2026-08-06:manual-verify", trigger_type="manual-verify")
        session.add(job)
        session.flush()
        assert compute_run_classification(session, job, job.run_key) == "adhoc"


def test_strict_clean_run_on_the_real_schedule_is_canonical(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        market = Market(platform_market_id="1", tracking_id="T-1", question="Q?", enabled=True)
        session.add(market)
        session.flush()
        job = _job(trigger_type="primary")
        session.add(job)
        session.flush()
        session.add(
            LLMForecast(
                market_id=market.id,
                run_slot="2026-08-06:primary",
                run_key=job.run_key,
                trigger_type="primary",
                model_provider="ollama",
                model_name="qwen3:8b",
                prompt_version="fair_value_v0.1",
                status="OK",
                evidence_all_live_classified=True,
            )
        )
        session.flush()
        assert compute_run_classification(session, job, job.run_key) == "canonical"


def test_backup_trigger_type_also_counts_as_canonical_schedule(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job(run_key="ppi-daily:2026-08-06:backup", trigger_type="backup")
        session.add(job)
        session.flush()
        # No forecasts -> vacuously uncontaminated, and "backup" is a canonical schedule trigger.
        assert compute_run_classification(session, job, job.run_key) == "canonical"


def test_mark_job_run_superseded_sets_pointer_without_touching_results(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        old = _job(run_key="ppi-daily:2026-08-06:manual-1", trigger_type="manual-1", run_classification="contaminated")
        new = _job(run_key="ppi-daily:2026-08-06:manual-2", trigger_type="manual-2", run_classification="adhoc")
        session.add_all([old, new])
        session.flush()
        mark_job_run_superseded(session, old, new)

        assert old.superseded_by_id == new.id
        assert old.run_classification == "contaminated"  # untouched
        assert new.superseded_by_id is None


def test_mark_job_run_superseded_rejects_self_reference(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job()
        session.add(job)
        session.flush()
        with pytest.raises(ValueError):
            mark_job_run_superseded(session, job, job)


def test_is_canonical_and_current():
    canonical_unsuperseded = JobRun(run_classification="canonical", superseded_by_id=None)
    canonical_superseded = JobRun(run_classification="canonical", superseded_by_id=5)
    contaminated = JobRun(run_classification="contaminated", superseded_by_id=None)

    assert is_canonical_and_current(canonical_unsuperseded) is True
    assert is_canonical_and_current(canonical_superseded) is False
    assert is_canonical_and_current(contaminated) is False
