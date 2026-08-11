from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import BlindIndexRun, JobRun, LLMForecast, Market
from app.ppi.run_health import compute_run_health, render_run_health_markdown


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'run_health.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _job(**kwargs) -> JobRun:
    defaults = dict(
        run_key="ppi-daily:2026-08-11:primary",
        job_name="daily_pipeline",
        trigger_type="primary",
        status="OK",
        pipeline_mode="strict_llm_only",
        run_classification="canonical",
        markets_attempted=3,
        evidence_discovered=10,
        evidence_classification_failed=3,
        llm_fallback_count=0,
        snapshots_written=3,
    )
    defaults.update(kwargs)
    return JobRun(**defaults)


def _market(session, tracking_id="T-1") -> Market:
    market = Market(platform_market_id=tracking_id, tracking_id=tracking_id, question="Will it happen?", enabled=True)
    session.add(market)
    session.flush()
    return market


def _forecast(job: JobRun, market: Market, *, status: str, raw_ppi: float | None = None) -> LLMForecast:
    return LLMForecast(
        market_id=market.id,
        job_run_id=job.id,
        run_key=job.run_key,
        run_slot=f"2026-08-11:{status.lower()}-{market.id}",
        trigger_type=job.trigger_type,
        model_provider="ollama",
        model_name="qwen3:8b",
        prompt_version="fair_value_v0.1",
        status=status,
        raw_ppi=raw_ppi,
    )


def test_empty_run_reports_all_zero_without_crashing(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job(markets_attempted=0, evidence_discovered=0, evidence_classification_failed=0, snapshots_written=0)
        session.add(job)
        session.flush()
        job_id = job.id

    with Session() as session:
        health = compute_run_health(session, job_id)
        assert health.forecasts_ok == 0
        assert health.forecasts_abstained == 0
        assert health.forecasts_error == 0
        assert health.evidence_attempted == 0
        assert health.evidence_successful == 0
        assert health.ppi_rows_persisted == 0
        assert health.blind_index_rows_persisted == 0


def test_forecast_status_buckets_ok_abstained_and_error(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job()
        session.add(job)
        session.flush()
        m1, m2, m3, m4, m5 = (_market(session, f"T-{i}") for i in range(1, 6))
        session.add_all(
            [
                _forecast(job, m1, status="OK", raw_ppi=0.05),
                _forecast(job, m2, status="OK", raw_ppi=-0.02),
                _forecast(job, m3, status="ABSTAINED"),
                _forecast(job, m4, status="FAILED"),
                _forecast(job, m5, status="SKIPPED_PROVIDER"),
            ]
        )
        session.flush()
        job_id = job.id

    with Session() as session:
        health = compute_run_health(session, job_id)
        # FAILED and SKIPPED_PROVIDER both mean "no live probability" -- neither is a fallback
        # value (LLMForecast has no fallback path at all) -- so both count as error.
        assert health.forecasts_ok == 2
        assert health.forecasts_abstained == 1
        assert health.forecasts_error == 2
        # Only the two OK forecasts were joined to a price (raw_ppi set).
        assert health.ppi_rows_persisted == 2


def test_evidence_counts_and_fallback_count_come_from_jobrun_columns(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job(evidence_discovered=10, evidence_classification_failed=3, llm_fallback_count=4)
        session.add(job)
        session.flush()
        job_id = job.id

    with Session() as session:
        health = compute_run_health(session, job_id)
        assert health.evidence_attempted == 10
        assert health.evidence_successful == 7
        assert health.llm_fallback_count == 4


def test_blind_index_rows_persisted_matches_only_this_run_key(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job(run_key="ppi-daily:2026-08-11:primary")
        session.add(job)
        session.flush()
        session.add(
            BlindIndexRun(
                job_run_id=job.id,
                run_key=job.run_key,
                market_count=2,
                model_name="qwen3:8b",
                prompt_version="fair_value_v0.1",
            )
        )
        # A row for a different run_key must not be counted against this job.
        other_job = _job(run_key="ppi-daily:2026-08-10:primary")
        session.add(other_job)
        session.flush()
        session.add(
            BlindIndexRun(
                job_run_id=other_job.id,
                run_key=other_job.run_key,
                market_count=2,
                model_name="qwen3:8b",
                prompt_version="fair_value_v0.1",
            )
        )
        session.flush()
        job_id = job.id

    with Session() as session:
        health = compute_run_health(session, job_id)
        assert health.blind_index_rows_persisted == 1


def test_unknown_job_run_id_raises(tmp_path):
    Session = _session_factory(tmp_path)
    with Session() as session:
        with pytest.raises(ValueError):
            compute_run_health(session, 999999)


def test_render_run_health_markdown_contains_all_fields_and_no_secrets(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job()
        session.add(job)
        session.flush()
        job_id = job.id

    with Session() as session:
        health = compute_run_health(session, job_id)

    markdown = render_run_health_markdown(health)
    assert "Forecasts OK" in markdown
    assert "Forecasts abstained" in markdown
    assert "Forecasts error" in markdown
    assert "LLM fallback count" in markdown
    assert "PPI rows persisted" in markdown
    assert "Blind-index rows persisted" in markdown
    assert str(health.job_run_id) in markdown
    assert health.run_classification in markdown
    for forbidden in ("DATABASE_URL", "postgres://", "postgresql://", "CLOUDFLARE_API_TOKEN"):
        assert forbidden not in markdown
