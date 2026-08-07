from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.database import Base
from app.db.models import BlindIndexRun, EvidenceItem, JobRun, LLMForecast, Market, MarketSource
from app.ppi.blind_forecast import FORBIDDEN_PACKET_KEYS, compute_and_persist_blind_index
from app.ppi.evidence import EvidenceCandidate, insert_and_classify_candidate


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'strict.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _make_market(session, tracking_id="T-1") -> Market:
    market = Market(platform_market_id=tracking_id, tracking_id=tracking_id, question="Will it happen?", enabled=True)
    session.add(market)
    session.flush()
    return market


class _BrokenClassifier:
    name = "ollama"

    def classify(self, payload):
        raise ValueError("model returned unparseable JSON")


class _WorkingClassifier:
    name = "ollama"

    def classify(self, payload):
        from app.ppi.classifier import EvidenceClassification

        return EvidenceClassification(
            relevant=True,
            relevance_score=0.9,
            source_quality=0.8,
            changes_probability=True,
            direction="YES",
            estimated_magnitude=0.1,
            category="news",
            summary="summary",
            reason="reason",
            needs_human_review=False,
        )


def test_strict_classification_failure_records_explicit_failure_not_fallback(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    monkeypatch.setattr("app.ppi.evidence.get_classifier", lambda: _BrokenClassifier())

    with Session.begin() as session:
        market = _make_market(session)
        candidate = EvidenceCandidate("rss", "Google News RSS", "Some headline", "https://example.com/a")
        item, inserted = insert_and_classify_candidate(session, market, None, candidate, strict=True)

    assert inserted is True
    assert item.review_status == "CLASSIFICATION_FAILED"
    assert item.classifier_provider == "ollama_failed"
    assert item.relevant is None  # never defaulted to True/False, and never deterministic-classified
    assert "no fallback was used" in item.reason


def test_strict_classification_success_behaves_normally(tmp_path, monkeypatch):
    Session = _session_factory(tmp_path)
    monkeypatch.setattr("app.ppi.evidence.get_classifier", lambda: _WorkingClassifier())

    with Session.begin() as session:
        market = _make_market(session)
        candidate = EvidenceCandidate("rss", "Google News RSS", "Some headline", "https://example.com/a")
        item, inserted = insert_and_classify_candidate(session, market, None, candidate, strict=True)

    assert inserted is True
    assert item.relevant is True
    assert item.classifier_provider != "deterministic_fallback"
    assert item.classifier_provider != "ollama_failed"
    assert item.review_status != "CLASSIFICATION_FAILED"


def test_strict_mode_never_falls_back_even_when_deterministic_would_succeed(tmp_path, monkeypatch):
    """A non-strict call with the same broken classifier would silently fall back and succeed;
    strict must not, since the whole point is no deterministic substitute in the canonical series."""
    Session = _session_factory(tmp_path)
    # classify_with_fallback() resolves its own get_classifier() via classifier.py's namespace,
    # not evidence.py's imported reference, so both must be patched for a consistent scenario.
    monkeypatch.setattr("app.ppi.evidence.get_classifier", lambda: _BrokenClassifier())
    monkeypatch.setattr("app.ppi.classifier.get_classifier", lambda: _BrokenClassifier())

    with Session.begin() as session:
        market = _make_market(session)
        candidate = EvidenceCandidate("rss", "Google News RSS", "poll shows a close race", "https://reuters.com/a")

        non_strict_item, _ = insert_and_classify_candidate(session, market, None, candidate, strict=False)
        assert non_strict_item.classifier_provider == "deterministic_fallback"

        candidate2 = EvidenceCandidate("rss", "Google News RSS", "poll shows a close race 2", "https://reuters.com/b")
        strict_item, _ = insert_and_classify_candidate(session, market, None, candidate2, strict=True)
        assert strict_item.classifier_provider == "ollama_failed"
        assert strict_item.relevant is None


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


def test_run_daily_pipeline_refuses_strict_mode_without_live_provider(tmp_path, monkeypatch):
    from app.ppi import pipeline as pipeline_module

    Session = _session_factory(tmp_path)
    settings = Settings(llm_provider="deterministic")
    monkeypatch.setattr(pipeline_module, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline_module, "init_db", lambda: None)
    monkeypatch.setattr(pipeline_module, "get_session", lambda: _fake_get_session(Session))

    result = pipeline_module.run_daily_pipeline(
        "test-trigger", strict_llm_only=True, lock_path=tmp_path / "pipeline.lock"
    )

    assert result["status"] == "FAILED"
    assert "strict_llm_only requires" in result["error"]

    with Session() as session:
        job = session.scalar(select(JobRun).where(JobRun.run_key == result["run_key"]))
        assert job.pipeline_mode == "strict_llm_only"
        assert job.markets_attempted == 0  # refused before touching any market
        assert session.scalar(select(EvidenceItem)) is None
        assert session.scalar(select(MarketSource)) is None


def test_force_rerun_resets_llm_forecast_counters_not_just_legacy_ones(tmp_path, monkeypatch):
    """Regression test for a real bug found during a live rerun: forcing a rerun of an existing
    JobRun reset markets/evidence/snapshot counters but not llm_forecasts_*, so they silently
    accumulated across reruns (e.g. 12 + 12 = 24) instead of reflecting only the latest run."""
    from app.ppi import pipeline as pipeline_module

    Session = _session_factory(tmp_path)
    settings = Settings(llm_provider="deterministic")
    monkeypatch.setattr(pipeline_module, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline_module, "init_db", lambda: None)
    monkeypatch.setattr(pipeline_module, "get_session", lambda: _fake_get_session(Session))

    with Session.begin() as session:
        job = JobRun(
            run_key="ppi-daily:2026-08-06:manual",
            job_name="daily_pipeline",
            trigger_type="manual",
            status="OK",
            llm_forecasts_attempted=12,
            llm_forecasts_succeeded=12,
        )
        session.add(job)

    # No markets are configured, so the per-market loop body is a no-op; only the top-of-function
    # reset logic runs. force=True with an existing OK job takes the "reset in place" branch.
    result = pipeline_module.run_daily_pipeline(
        "manual", force=True, run_date=date(2026, 8, 6), lock_path=tmp_path / "pipeline.lock"
    )

    with Session() as session:
        job = session.scalar(select(JobRun).where(JobRun.run_key == result["run_key"]))
        assert job.llm_forecasts_attempted == 0
        assert job.llm_forecasts_succeeded == 0


def _forecast(market_id: int, run_key: str, raw_ppi: float | None, **kwargs) -> LLMForecast:
    defaults = dict(
        market_id=market_id,
        run_slot=f"slot-{market_id}",
        run_key=run_key,
        trigger_type="manual",
        generated_at=datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc),
        model_provider="ollama",
        model_name="qwen3:8b",
        prompt_version="fair_value_v0.1",
        status="OK",
        fair_value=0.5,
        raw_ppi=raw_ppi,
    )
    defaults.update(kwargs)
    return LLMForecast(**defaults)


def test_compute_and_persist_blind_index_math_and_idempotent_upsert(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        market_ids = []
        for i in range(3):
            m = _make_market(session, tracking_id=f"T-{i}")
            market_ids.append(m.id)
        job = JobRun(run_key="run-x", job_name="daily_pipeline", trigger_type="manual")
        session.add(job)
        session.flush()

        # Signed premiums 0.10, -0.20, 0.30 -> mean=0.0666..., median=0.10, mean(abs)=0.20
        session.add(_forecast(market_ids[0], "run-x", 0.10))
        session.add(_forecast(market_ids[1], "run-x", -0.20))
        session.add(_forecast(market_ids[2], "run-x", 0.30))
        # A forecast from a different run_key must not be included.
        session.add(_forecast(market_ids[0], "other-run", 0.99, run_slot="other-slot"))
        session.flush()

        row = compute_and_persist_blind_index(session, job, "run-x")
        first_id = row.id

    assert row.market_count == 3
    assert row.average_signed_premium == pytest.approx((0.10 - 0.20 + 0.30) / 3)
    assert row.median_signed_premium == pytest.approx(0.10)
    assert row.average_absolute_premium == pytest.approx((0.10 + 0.20 + 0.30) / 3)
    assert row.model_name == "qwen3:8b"

    # Rerun: must upsert the same row, not append a duplicate.
    with Session.begin() as session:
        job = session.scalar(select(JobRun).where(JobRun.run_key == "run-x"))
        row2 = compute_and_persist_blind_index(session, job, "run-x")
        assert row2.id == first_id
        all_rows = list(session.scalars(select(BlindIndexRun).where(BlindIndexRun.run_key == "run-x")))
        assert len(all_rows) == 1


def test_forbidden_keys_cover_ppi_and_aggregate_field_names():
    ppi_and_aggregate_fields = {
        "raw_ppi",
        "comparison_price_at_join",
        "average_signed_premium",
        "median_signed_premium",
        "average_absolute_premium",
    }
    assert ppi_and_aggregate_fields <= FORBIDDEN_PACKET_KEYS
