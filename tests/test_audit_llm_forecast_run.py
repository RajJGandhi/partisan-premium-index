from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import EvidenceItem, JobRun, LLMForecast, Market
from scripts.audit_llm_forecast_run import (
    _assert_report_is_safe,
    audit_job_run,
    render_markdown_summary,
)


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
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
    )
    defaults.update(kwargs)
    return JobRun(**defaults)


def _market(session, tracking_id) -> Market:
    market = Market(
        platform_market_id=tracking_id,
        tracking_id=tracking_id,
        slug=f"slug-{tracking_id}",
        question=f"Will {tracking_id} happen?",
        category="politics",
        region="US",
        rules=f"Resolves YES if {tracking_id} happens by the end date.",
        enabled=True,
    )
    session.add(market)
    session.flush()
    return market


def _evidence(
    session, market_id, *, title, content_hash, published_at, classifier_provider="ollama", summary=None
) -> EvidenceItem:
    item = EvidenceItem(
        market_id=market_id,
        source_type="rss",
        source_name="Example feed",
        title=title,
        summary=summary or f"Summary of {title}",
        category="news",
        canonical_url=f"https://example.com/{content_hash}",
        published_at=published_at,
        normalized_title=title.lower(),
        content_hash=content_hash,
        relevant=True,
        classifier_provider=classifier_provider,
        review_status="AUTO_ACCEPTED",
    )
    session.add(item)
    session.flush()
    return item


def _forecast(job, market_id, *, evidence_ids, raw_response, fair_value, retries=0, run_slot=None) -> LLMForecast:
    return LLMForecast(
        market_id=market_id,
        job_run_id=job.id,
        run_key=job.run_key,
        run_slot=run_slot or f"2026-08-11:m{market_id}",
        trigger_type=job.trigger_type,
        generated_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
        model_provider="ollama",
        model_name="qwen3:8b",
        prompt_version="fair_value_v0.1",
        prompt_hash="hash",
        generation_params_json=json.dumps({"temperature": 0.15, "num_ctx": 4096, "max_retries": 2}),
        evidence_ids_json=json.dumps(evidence_ids),
        evidence_all_live_classified=True,
        raw_response=raw_response,
        fair_value=fair_value,
        confidence=0.6,
        should_abstain=False,
        rationale="rationale text",
        status="OK",
        retries=retries,
    )


def _row_counts(session_factory) -> dict[str, int]:
    with session_factory() as session:
        return {
            "job_runs": len(list(session.scalars(select(JobRun)))),
            "markets": len(list(session.scalars(select(Market)))),
            "llm_forecasts": len(list(session.scalars(select(LLMForecast)))),
            "evidence_items": len(list(session.scalars(select(EvidenceItem)))),
        }


def _build_fixture(tmp_path):
    """Two markets sharing one duplicated evidence article, plus a distinct third market,
    modeling the exact anomaly under investigation: overlapping evidence and a repeated
    probability value across otherwise-different markets."""
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        job = _job()
        session.add(job)
        session.flush()

        market_a = _market(session, "A")
        market_b = _market(session, "B")
        market_c = _market(session, "C")

        shared = _evidence(
            session,
            market_a.id,
            title="Shared generic article",
            content_hash="hash-shared",
            published_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        # Same underlying article, independently discovered/inserted for market_b (different
        # EvidenceItem row and id, same content_hash -- exactly how cross-market duplication
        # happens per app/ppi/evidence.py's per-market dedup).
        shared_b = _evidence(
            session,
            market_b.id,
            title="Shared generic article",
            content_hash="hash-shared",
            published_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        unique_c = _evidence(
            session,
            market_c.id,
            title="Market C specific article",
            content_hash="hash-c-only",
            published_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        )

        forecast_a = _forecast(
            job,
            market_a.id,
            evidence_ids=[shared.id],
            raw_response='{"fair_value": 0.45, "confidence": 0.6}',
            fair_value=0.45,
            run_slot="2026-08-11:a",
        )
        forecast_b = _forecast(
            job,
            market_b.id,
            evidence_ids=[shared_b.id],
            raw_response="<think>hedging...</think>\n{\"fair_value\": 0.45, \"confidence\": 0.6}",
            fair_value=0.45,
            retries=1,
            run_slot="2026-08-11:b",
        )
        forecast_c = _forecast(
            job,
            market_c.id,
            evidence_ids=[unique_c.id],
            raw_response='{"fair_value": 0.62, "confidence": 0.7}',
            fair_value=0.62,
            run_slot="2026-08-11:c",
        )
        session.add_all([forecast_a, forecast_b, forecast_c])
        session.flush()
        job_id = job.id

    return Session, job_id


def test_audit_reports_per_forecast_fields_correctly(tmp_path):
    Session, job_id = _build_fixture(tmp_path)
    with Session() as session:
        report = audit_job_run(session, job_id)

    assert report["job_run_id"] == job_id
    assert report["run_classification"] == "canonical"
    assert len(report["forecasts"]) == 3

    by_slot = {r["run_slot"]: r for r in report["forecasts"]}
    a = by_slot["2026-08-11:a"]
    assert a["fair_value"] == 0.45
    assert a["evidence_count"] == 1
    assert a["evidence_items"][0]["title"] == "Shared generic article"
    assert a["evidence_items"][0]["content_hash"] == "hash-shared"
    assert a["evidence_items"][0]["summary"] == "Summary of Shared generic article"
    assert a["evidence_items"][0]["category"] == "news"
    assert a["raw_response_contains_target_probability"] is True
    assert a["thinking_mode_detected"] is False
    assert a["raw_response_hash"] is not None
    # Market-level fields needed to reconstruct the exact blind evidence packet for replay.
    assert a["market_category"] == "politics"
    assert a["market_region"] == "US"
    assert a["market_resolution_criteria"] == "Resolves YES if A happens by the end date."

    b = by_slot["2026-08-11:b"]
    assert b["retries"] == 1
    assert b["thinking_mode_detected"] is True
    assert b["generation_params"] == {"temperature": 0.15, "num_ctx": 4096, "max_retries": 2}


def test_aggregate_counts_unique_values_and_target_probability(tmp_path):
    Session, job_id = _build_fixture(tmp_path)
    with Session() as session:
        report = audit_job_run(session, job_id)

    agg = report["aggregate"]
    assert agg["forecast_count"] == 3
    assert agg["unique_probability_count"] == 2  # 0.45 (x2) and 0.62
    assert agg["count_at_target_probability"] == 2
    assert agg["unique_raw_response_count"] == 3  # all three raw_response strings differ
    assert agg["thinking_mode_detected_count"] == 1


def test_evidence_overlap_and_repeated_sources_detected_by_content_hash(tmp_path):
    Session, job_id = _build_fixture(tmp_path)
    with Session() as session:
        report = audit_job_run(session, job_id)

    agg = report["aggregate"]
    assert len(agg["evidence_pair_overlaps"]) == 1
    overlap = agg["evidence_pair_overlaps"][0]
    assert overlap["shared_content_hashes"] == ["hash-shared"]

    assert len(agg["repeated_evidence_sources"]) == 1
    repeated = agg["repeated_evidence_sources"][0]
    assert repeated["content_hash"] == "hash-shared"
    assert repeated["market_count"] == 2


def test_identical_evidence_packets_detected(tmp_path):
    """Two forecasts whose evidence packets contain exactly the same content hashes (even from
    different EvidenceItem rows/ids) must be flagged as identical packets."""
    Session, job_id = _build_fixture(tmp_path)
    with Session() as session:
        report = audit_job_run(session, job_id)

    forecasts_by_slot = {r["run_slot"]: r for r in report["forecasts"]}
    assert forecasts_by_slot["2026-08-11:a"]["evidence_packet_hash"] == forecasts_by_slot["2026-08-11:b"][
        "evidence_packet_hash"
    ]
    assert forecasts_by_slot["2026-08-11:a"]["evidence_packet_hash"] in report["aggregate"]["identical_evidence_packets"]


def test_unknown_job_run_id_raises(tmp_path):
    Session = _session_factory(tmp_path)
    with Session() as session:
        with pytest.raises(ValueError):
            audit_job_run(session, 999999)


def test_audit_performs_no_database_writes(tmp_path):
    """The core anti-regression test: auditing a run must never mutate it. Verified two ways --
    (1) row counts and every forecast's persisted fields are byte-identical before and after, and
    (2) Session.add/delete/merge are never invoked at all during the audit."""
    Session, job_id = _build_fixture(tmp_path)

    before_counts = _row_counts(Session)
    with Session() as session:
        before_forecasts = {
            f.id: (f.fair_value, f.raw_response, f.retries, f.status, f.evidence_ids_json)
            for f in session.scalars(select(LLMForecast))
        }
        before_job = session.get(JobRun, job_id)
        before_superseded = before_job.superseded_by_id
        before_classification = before_job.run_classification

    calls: list[str] = []

    with Session() as session:
        def _tracking_add(*args, **kwargs):
            calls.append("add")
            return Session.add(session, *args, **kwargs)  # would only run if actually called

        def _tracking_delete(*args, **kwargs):
            calls.append("delete")
            return Session.delete(session, *args, **kwargs)

        def _tracking_merge(*args, **kwargs):
            calls.append("merge")
            return Session.merge(session, *args, **kwargs)

        session.add = _tracking_add  # type: ignore[method-assign]
        session.delete = _tracking_delete  # type: ignore[method-assign]
        session.merge = _tracking_merge  # type: ignore[method-assign]

        audit_job_run(session, job_id)

    assert calls == [], f"audit_job_run invoked mutating session methods: {calls}"

    after_counts = _row_counts(Session)
    assert after_counts == before_counts

    with Session() as session:
        after_forecasts = {
            f.id: (f.fair_value, f.raw_response, f.retries, f.status, f.evidence_ids_json)
            for f in session.scalars(select(LLMForecast))
        }
        after_job = session.get(JobRun, job_id)
        assert after_job.superseded_by_id == before_superseded
        assert after_job.run_classification == before_classification

    assert after_forecasts == before_forecasts


def test_markdown_summary_excludes_raw_response_and_evidence_text(tmp_path):
    Session, job_id = _build_fixture(tmp_path)
    with Session() as session:
        report = audit_job_run(session, job_id)

    summary = render_markdown_summary(report)
    assert "hash-shared" not in summary
    assert "Shared generic article" not in summary
    assert "hedging" not in summary  # raw_response content must not leak into the step summary
    assert "0.45" in summary  # aggregate count is fine to show
    assert "Job run ID" in summary


def test_assert_report_is_safe_catches_planted_secret_marker():
    with pytest.raises(ValueError):
        _assert_report_is_safe({"forecasts": [{"raw_response": "postgresql://user:pass@host/db"}]})
    # A clean report does not raise.
    _assert_report_is_safe({"forecasts": [{"raw_response": "fair_value: 0.45"}]})
