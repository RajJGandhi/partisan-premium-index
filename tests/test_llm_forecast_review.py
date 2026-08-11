from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import LLMForecast, Market
from app.ppi.llm_forecast_review import set_llm_forecast_review_status


def _session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _make_forecast(session, *, status: str = "OK", fair_value: float | None = 0.42) -> LLMForecast:
    unique = uuid.uuid4().hex[:8]
    market = Market(platform_market_id=unique, tracking_id=f"T-{unique}", question="Test?", enabled=True)
    session.add(market)
    session.flush()
    forecast = LLMForecast(
        market_id=market.id,
        run_slot="2026-08-05:primary",
        run_key="run-1",
        trigger_type="manual",
        generated_at=datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc),
        model_provider="ollama",
        model_name="qwen3:8b",
        prompt_version="fair_value_v0.1",
        prompt_hash="hash",
        status=status,
        fair_value=fair_value,
        confidence=0.7,
        should_abstain=False,
        rationale="Original rationale.",
        raw_response='{"fair_value": 0.42}',
    )
    session.add(forecast)
    session.flush()
    return forecast


def test_flagging_never_mutates_the_immutable_forecast_fields(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        forecast = _make_forecast(session)
        before = {
            "fair_value": forecast.fair_value,
            "confidence": forecast.confidence,
            "should_abstain": forecast.should_abstain,
            "rationale": forecast.rationale,
            "raw_response": forecast.raw_response,
            "status": forecast.status,
            "generated_at": forecast.generated_at,
        }

        set_llm_forecast_review_status(session, forecast, "FLAGGED", "reviewer-a", "possible bad evidence")

        assert forecast.fair_value == before["fair_value"]
        assert forecast.confidence == before["confidence"]
        assert forecast.should_abstain == before["should_abstain"]
        assert forecast.rationale == before["rationale"]
        assert forecast.raw_response == before["raw_response"]
        assert forecast.status == before["status"]
        assert forecast.generated_at == before["generated_at"]

        assert forecast.reviewed_status == "FLAGGED"
        assert forecast.reviewed_by == "reviewer-a"
        assert forecast.review_notes == "possible bad evidence"
        assert forecast.reviewed_at is not None


def test_flagging_and_resetting_only_touch_review_fields(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        forecast = _make_forecast(session)
        set_llm_forecast_review_status(session, forecast, "FLAGGED", "reviewer-b", "possible bad evidence")
        assert forecast.reviewed_status == "FLAGGED"
        assert forecast.fair_value == 0.42

        set_llm_forecast_review_status(session, forecast, "UNREVIEWED", "reviewer-b", None)
        assert forecast.reviewed_status == "UNREVIEWED"
        assert forecast.fair_value == 0.42


def test_unknown_review_status_is_rejected(tmp_path):
    """"APPROVED_FOR_PUBLICATION" no longer exists -- publication is automatic for a canonical
    forecast (see app.ppi.public_forecast), so "approving" is not a review action anymore."""
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        forecast = _make_forecast(session)
        with pytest.raises(ValueError):
            set_llm_forecast_review_status(session, forecast, "APPROVED_FOR_PUBLICATION", "reviewer-a")
        with pytest.raises(ValueError):
            set_llm_forecast_review_status(session, forecast, "PUBLISHED", "reviewer-a")


def test_missing_reviewer_is_rejected(tmp_path):
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        forecast = _make_forecast(session)
        with pytest.raises(ValueError):
            set_llm_forecast_review_status(session, forecast, "FLAGGED", "")


def test_can_flag_any_generation_status_including_failed_or_skipped(tmp_path):
    """Flagging is a data-integrity concern, not a publication gate, so it is not restricted to
    OK/ABSTAINED generation statuses the way the old "approve for publication" action was."""
    Session = _session_factory(tmp_path)
    with Session.begin() as session:
        failed = _make_forecast(session, status="FAILED", fair_value=None)
        set_llm_forecast_review_status(session, failed, "FLAGGED", "reviewer-a")
        assert failed.reviewed_status == "FLAGGED"

    with Session.begin() as session:
        skipped = _make_forecast(session, status="SKIPPED_PROVIDER", fair_value=None)
        set_llm_forecast_review_status(session, skipped, "FLAGGED", "reviewer-a")
        assert skipped.reviewed_status == "FLAGGED"
